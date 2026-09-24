#!/usr/bin/env python3
"""Render and validate the isolated Docker-host property-catalog DEV workload.

The renderer is deliberately local-only: it reads one reviewed YAML file and,
optionally, local host artifacts for a permissions/hash preflight.  It never
invokes Docker, Kafka, PostgreSQL, ClickHouse, SSH, or any network client.
"""

from __future__ import annotations

import argparse
import copy
import ipaddress
import os
import re
import stat
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

FORMAT = "futureagi.property-catalog-dev-docker"
OSS_FORMAT = "futureagi.property-catalog-oss-dev-docker"
VERSION = 1
RUNTIME_UID = 65_532
RUNTIME_GID = 65_532
CONTAINER_RUNTIME_DIRECTORY = "/var/lib/property-catalog-runtime"
CONTAINER_SPOOL_DIRECTORY = f"{CONTAINER_RUNTIME_DIRECTORY}/catalog-spool"
REVISION_FENCE_FILE = f"{CONTAINER_SPOOL_DIRECTORY}/revision-fence-v2.json"
DRAIN_PROOF_FILE = f"{CONTAINER_SPOOL_DIRECTORY}/producer-drain-proof-v2.json"
PRODUCER_RETIREMENT_FILE = (
    f"{CONTAINER_SPOOL_DIRECTORY}/producer-state-retirements-v1.json"
)
ROLLOUT_ACK = "FI_PROPERTY_CATALOG_DEV"
SIDECAR_ACK = "FI_PROPERTY_CATALOG_PYTHON_GO_SIDECAR_V1"
GO_DEV_ACK = "FI_PROPERTY_CATALOG_V1_DEV_ONLY"
RUNTIME_FACTORY = (
    "tracer.services.clickhouse.v2.property_catalog.dev_runtime."
    "configured_property_catalog_dev_runtime"
)

_TOP_LEVEL_FIELDS = (
    "format",
    "version",
    "deployment_id",
    "images",
    "host",
    "workspace",
    "catalog",
    "infrastructure",
    "provenance",
)
_IMAGE_FIELDS = ("collector_runtime", "operator")
_HOST_FIELDS = ("root",)
_WORKSPACE_FIELDS = ("organization_id", "workspace_id", "project_ids")
_CATALOG_FIELDS = (
    "epoch",
    "projection_version",
    "hot_producer_stream_id",
    "source_database",
    "target_database",
    "span_since",
    "span_until",
    "dev_identity",
)
_INFRASTRUCTURE_FIELDS = (
    "application_docker_network",
    "kafka_docker_network",
    "source_clickhouse_host",
    "source_clickhouse_native_port",
    "source_clickhouse_http_port",
    "target_clickhouse_host",
    "target_clickhouse_native_port",
    "target_clickhouse_http_port",
    "kafka_brokers",
)
_PROVENANCE_FIELDS = (
    "write_clickhouse_hostname",
    "source_clickhouse_hostname",
    "postgres_database",
    "postgres_user",
    "postgres_server_address",
    "postgres_server_port",
)

_ENV_FILE_KEYS: Mapping[str, tuple[str, ...]] = {
    "producer.env": ("FI_PG_WRITE", "FI_CH_USERNAME", "FI_CH_PASSWORD"),
    "operator-runtime.env": ("SECRET_KEY",),
    "operator-postgres.env": (
        "PGBOUNCER_HOST",
        "PGBOUNCER_PORT",
        "PG_DB",
        "PG_USER",
        "PG_PASSWORD",
    ),
    "operator-source-clickhouse.env": ("CH25_USER", "CH25_PASSWORD"),
    "operator-target-clickhouse.env": (
        "PROPERTY_CATALOG_DEV_WRITE_CH_USER",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD",
    ),
    "consumer-write-clickhouse.env": (
        "FI_PROPERTY_CATALOG_CH_USERNAME",
        "FI_PROPERTY_CATALOG_CH_PASSWORD",
    ),
    "consumer-ledger-clickhouse.env": (
        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME",
        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD",
    ),
}

_EMPTY_ALLOWED_KEYS = {
    "FI_CH_PASSWORD",
    "CH25_PASSWORD",
    "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD",
    "FI_PROPERTY_CATALOG_CH_PASSWORD",
    "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD",
}
_SAFE_DEPLOYMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
_SAFE_NAME_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?$", re.I)
_SAFE_DOCKER_NAME_RE = re.compile(r"^[a-z0-9](?:[-_a-z0-9.]{0,126}[a-z0-9])?$", re.I)
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CATALOG_DATABASE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_RESERVED_CATALOG_DATABASES = frozenset(
    {"default", "futureagi", "information_schema", "property_catalog", "system"}
)
_DEV_IDENTITY_RE = re.compile(r"^dev:property-catalog/[a-z0-9][a-z0-9._:/-]{2,96}$")
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HOST_PORT_RE = re.compile(
    r"^(?P<host>[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?):(?P<port>[0-9]{1,5})$",
    re.I,
)
_PLACEHOLDER_RE = re.compile(
    r"(?:replace|placeholder|change[-_ ]?me|todo|example|<[^>]*>)",
    re.I,
)
_PRODUCTION_TOKEN_RE = re.compile(
    r"(?:^|[-._/:])(prod|production|live)(?:$|[-._/:])",
    re.I,
)
_IMAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:@-]{2,511}$")
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class DeploymentValidationError(ValueError):
    """The requested workload is not provably within the isolated DEV contract."""


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise DeploymentValidationError(
                "configuration mapping keys must be scalar"
            ) from exc
        if duplicate:
            raise DeploymentValidationError(
                f"configuration contains duplicate key {key!r}"
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    deployment_id: str
    collector_image: str
    operator_image: str
    host_root: str
    organization_id: str
    workspace_id: str
    project_ids: tuple[str, ...]
    epoch: int
    projection_version: int
    hot_producer_stream_id: str
    source_database: str
    target_database: str
    span_since: str
    span_until: str
    dev_identity: str
    application_docker_network: str
    kafka_docker_network: str
    source_clickhouse_host: str
    source_clickhouse_native_port: int
    source_clickhouse_http_port: int
    target_clickhouse_host: str
    target_clickhouse_native_port: int
    target_clickhouse_http_port: int
    kafka_brokers: tuple[str, ...]
    write_clickhouse_hostname: str
    source_clickhouse_hostname: str
    postgres_database: str
    postgres_user: str
    postgres_server_address: str
    postgres_server_port: int
    runtime_profile: str = "dev_cloud"

    @property
    def root(self) -> Path:
        return Path(self.host_root)

    @property
    def runtime_directory(self) -> Path:
        return self.root / "runtime"

    @property
    def private_directory(self) -> Path:
        return self.root / "private"

    @property
    def kafka_topic(self) -> str:
        return f"futureagi.dev.property-catalog.{self.deployment_id}"

    @property
    def kafka_consumer_group(self) -> str:
        return f"futureagi.dev.property-catalog.consumer.{self.deployment_id}"

    @property
    def source_clickhouse_http_url(self) -> str:
        return (
            f"http://{self.source_clickhouse_host}:{self.source_clickhouse_http_port}"
        )

    @property
    def target_clickhouse_http_url(self) -> str:
        return (
            f"http://{self.target_clickhouse_host}:{self.target_clickhouse_http_port}"
        )


def _mapping(value: Any, label: str, fields: Sequence[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DeploymentValidationError(f"{label} must be a mapping")
    actual = set(value)
    expected = set(fields)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise DeploymentValidationError(
            f"{label} fields differ; missing={missing}, extra={extra}"
        )
    return value


def _text(value: Any, label: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DeploymentValidationError(f"{label} must be non-empty trimmed text")
    if len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DeploymentValidationError(f"{label} is not bounded printable text")
    if _PLACEHOLDER_RE.search(value):
        raise DeploymentValidationError(f"{label} still contains a placeholder")
    return value


def _positive_int(value: Any, label: str, *, maximum: int = 65_535) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise DeploymentValidationError(f"{label} must be an integer in 1..{maximum}")
    return value


def _uuid4(value: Any, label: str) -> str:
    text = _text(value, label, limit=36)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise DeploymentValidationError(f"{label} must be a canonical UUIDv4") from exc
    if parsed.version != 4 or str(parsed) != text:
        raise DeploymentValidationError(f"{label} must be a canonical UUIDv4")
    return text


def _port(value: Any, label: str) -> int:
    return _positive_int(value, label, maximum=65_535)


def _host(value: Any, label: str) -> str:
    text = _text(value, label, limit=253)
    if _SAFE_NAME_RE.fullmatch(text) is None or _PRODUCTION_TOKEN_RE.search(text):
        raise DeploymentValidationError(f"{label} must be a non-production host name")
    return text


def _image(value: Any, label: str, *, required_token: str) -> str:
    text = _text(value, label)
    if (
        _IMAGE_RE.fullmatch(text) is None
        or _PRODUCTION_TOKEN_RE.search(text)
        or text.endswith(":latest")
        or required_token not in text.lower()
    ):
        raise DeploymentValidationError(
            f"{label} must be an explicit non-production {required_token} image reference"
        )
    last = text.rsplit("/", 1)[-1]
    if "@sha256:" not in text and ":" not in last:
        raise DeploymentValidationError(
            f"{label} must include an explicit tag or digest"
        )
    if "@sha256:" in text:
        digest = text.rsplit("@sha256:", 1)[-1]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise DeploymentValidationError(f"{label} has an invalid image digest")
    return text


def _local_image_id(value: Any, label: str) -> str:
    text = _text(value, label)
    digest = text.removeprefix("sha256:")
    if _IMAGE_ID_RE.fullmatch(text) is None or len(set(digest)) < 4:
        raise DeploymentValidationError(
            f"{label} must be an exact immutable local sha256:<64 lowercase hex> image ID"
        )
    return text


def _datetime_hour(value: Any, label: str) -> tuple[str, datetime]:
    text = _text(value, label, limit=32)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise DeploymentValidationError(
            f"{label} must be an hour-aligned UTC timestamp"
        ) from exc
    return text, parsed


def load_config(path: os.PathLike[str] | str) -> DeploymentConfig:
    config_path = Path(path)
    try:
        raw = yaml.load(
            config_path.read_text(encoding="utf-8"), Loader=_UniqueSafeLoader
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DeploymentValidationError(f"cannot read configuration: {exc}") from exc
    top = _mapping(raw, "configuration", _TOP_LEVEL_FIELDS)
    if top["format"] not in {FORMAT, OSS_FORMAT} or top["version"] != VERSION:
        raise DeploymentValidationError("configuration format/version is not supported")
    runtime_profile = "oss" if top["format"] == OSS_FORMAT else "dev_cloud"

    deployment_id = _text(top["deployment_id"], "deployment_id", limit=40)
    if _SAFE_DEPLOYMENT_RE.fullmatch(
        deployment_id
    ) is None or _PRODUCTION_TOKEN_RE.search(deployment_id):
        raise DeploymentValidationError(
            "deployment_id is not a safe DEV isolation suffix"
        )

    images = _mapping(top["images"], "images", _IMAGE_FIELDS)
    host = _mapping(top["host"], "host", _HOST_FIELDS)
    workspace = _mapping(top["workspace"], "workspace", _WORKSPACE_FIELDS)
    catalog = _mapping(top["catalog"], "catalog", _CATALOG_FIELDS)
    infrastructure = _mapping(
        top["infrastructure"], "infrastructure", _INFRASTRUCTURE_FIELDS
    )
    provenance = _mapping(top["provenance"], "provenance", _PROVENANCE_FIELDS)

    host_root = _text(host["root"], "host.root")
    expected_root = f"/home/ubuntu/property-catalog-{deployment_id}"
    if (
        host_root != expected_root
        or not Path(host_root).is_absolute()
        or os.path.normpath(host_root) != host_root
    ):
        raise DeploymentValidationError(
            f"host.root must equal the dedicated physical path {expected_root}"
        )

    project_values = workspace["project_ids"]
    if not isinstance(project_values, list) or not 1 <= len(project_values) <= 256:
        raise DeploymentValidationError(
            "workspace.project_ids must contain 1..256 UUIDv4s"
        )
    project_ids = tuple(
        _uuid4(value, f"workspace.project_ids[{index}]")
        for index, value in enumerate(project_values)
    )
    if tuple(sorted(set(project_ids))) != project_ids:
        raise DeploymentValidationError(
            "workspace.project_ids must be sorted and contain no duplicates"
        )

    source_database = _text(catalog["source_database"], "catalog.source_database")
    target_database = _text(catalog["target_database"], "catalog.target_database")
    if source_database != "futureagi":
        raise DeploymentValidationError("catalog.source_database must equal futureagi")
    if (
        _CATALOG_DATABASE_RE.fullmatch(target_database) is None
        or target_database in _RESERVED_CATALOG_DATABASES
        or target_database == source_database
    ):
        raise DeploymentValidationError(
            "catalog.target_database must be a safe lowercase ClickHouse identifier "
            "isolated from production and source databases"
        )
    since_text, since = _datetime_hour(catalog["span_since"], "catalog.span_since")
    until_text, until = _datetime_hour(catalog["span_until"], "catalog.span_until")
    window = until - since
    if window < timedelta(hours=1) or window > timedelta(days=366):
        raise DeploymentValidationError(
            "catalog span window must be in [1 hour, 366 days]"
        )

    dev_identity = _text(catalog["dev_identity"], "catalog.dev_identity", limit=128)
    if _DEV_IDENTITY_RE.fullmatch(dev_identity) is None or _PRODUCTION_TOKEN_RE.search(
        dev_identity
    ):
        raise DeploymentValidationError(
            "catalog.dev_identity is not DEV property-catalog scoped"
        )

    application_docker_network = _text(
        infrastructure["application_docker_network"],
        "infrastructure.application_docker_network",
        limit=128,
    )
    kafka_docker_network = _text(
        infrastructure["kafka_docker_network"],
        "infrastructure.kafka_docker_network",
        limit=128,
    )
    for label, network_name in (
        ("application", application_docker_network),
        ("Kafka", kafka_docker_network),
    ):
        if _SAFE_DOCKER_NAME_RE.fullmatch(
            network_name
        ) is None or _PRODUCTION_TOKEN_RE.search(network_name):
            raise DeploymentValidationError(
                f"{label} Docker network is not a safe non-production name"
            )
    if application_docker_network == kafka_docker_network:
        raise DeploymentValidationError(
            "application and Kafka must retain their distinct existing networks"
        )

    brokers_raw = infrastructure["kafka_brokers"]
    if not isinstance(brokers_raw, list) or not 1 <= len(brokers_raw) <= 16:
        raise DeploymentValidationError(
            "infrastructure.kafka_brokers must contain 1..16 brokers"
        )
    brokers: list[str] = []
    for index, value in enumerate(brokers_raw):
        broker = _text(value, f"infrastructure.kafka_brokers[{index}]", limit=255)
        match = _HOST_PORT_RE.fullmatch(broker)
        if match is None or not 1 <= int(match.group("port")) <= 65_535:
            raise DeploymentValidationError(
                "Kafka brokers must be host:port, never URLs"
            )
        if _PRODUCTION_TOKEN_RE.search(broker):
            raise DeploymentValidationError("Kafka broker contains a production token")
        brokers.append(broker)
    if tuple(sorted(set(brokers))) != tuple(brokers):
        raise DeploymentValidationError("Kafka brokers must be sorted and unique")

    pg_address = _text(
        provenance["postgres_server_address"],
        "provenance.postgres_server_address",
        limit=64,
    )
    try:
        parsed_pg_address = ipaddress.ip_address(pg_address)
    except ValueError as exc:
        raise DeploymentValidationError(
            "provenance.postgres_server_address must be a literal IP"
        ) from exc
    if str(parsed_pg_address) != pg_address:
        raise DeploymentValidationError(
            "provenance.postgres_server_address must be canonical"
        )

    operator_image = _image(
        images["operator"], "images.operator", required_token="property-catalog"
    )
    if runtime_profile == "oss" and "oss" not in operator_image.lower():
        raise DeploymentValidationError(
            "images.operator must identify the reviewed OSS backend image"
        )

    result = DeploymentConfig(
        deployment_id=deployment_id,
        collector_image=_local_image_id(
            images["collector_runtime"], "images.collector_runtime"
        ),
        operator_image=operator_image,
        host_root=host_root,
        organization_id=_uuid4(
            workspace["organization_id"], "workspace.organization_id"
        ),
        workspace_id=_uuid4(workspace["workspace_id"], "workspace.workspace_id"),
        project_ids=project_ids,
        epoch=_positive_int(catalog["epoch"], "catalog.epoch"),
        projection_version=_positive_int(
            catalog["projection_version"], "catalog.projection_version"
        ),
        hot_producer_stream_id=_uuid4(
            catalog["hot_producer_stream_id"], "catalog.hot_producer_stream_id"
        ),
        source_database=source_database,
        target_database=target_database,
        span_since=since_text,
        span_until=until_text,
        dev_identity=dev_identity,
        application_docker_network=application_docker_network,
        kafka_docker_network=kafka_docker_network,
        source_clickhouse_host=_host(
            infrastructure["source_clickhouse_host"],
            "infrastructure.source_clickhouse_host",
        ),
        source_clickhouse_native_port=_port(
            infrastructure["source_clickhouse_native_port"],
            "infrastructure.source_clickhouse_native_port",
        ),
        source_clickhouse_http_port=_port(
            infrastructure["source_clickhouse_http_port"],
            "infrastructure.source_clickhouse_http_port",
        ),
        target_clickhouse_host=_host(
            infrastructure["target_clickhouse_host"],
            "infrastructure.target_clickhouse_host",
        ),
        target_clickhouse_native_port=_port(
            infrastructure["target_clickhouse_native_port"],
            "infrastructure.target_clickhouse_native_port",
        ),
        target_clickhouse_http_port=_port(
            infrastructure["target_clickhouse_http_port"],
            "infrastructure.target_clickhouse_http_port",
        ),
        kafka_brokers=tuple(brokers),
        write_clickhouse_hostname=_host(
            provenance["write_clickhouse_hostname"],
            "provenance.write_clickhouse_hostname",
        ),
        source_clickhouse_hostname=_host(
            provenance["source_clickhouse_hostname"],
            "provenance.source_clickhouse_hostname",
        ),
        postgres_database=_text(
            provenance["postgres_database"], "provenance.postgres_database", limit=128
        ),
        postgres_user=_text(
            provenance["postgres_user"], "provenance.postgres_user", limit=128
        ),
        postgres_server_address=pg_address,
        postgres_server_port=_port(
            provenance["postgres_server_port"], "provenance.postgres_server_port"
        ),
        runtime_profile=runtime_profile,
    )
    if _SAFE_IDENTIFIER_RE.fullmatch(result.postgres_database) is None:
        raise DeploymentValidationError(
            "provenance.postgres_database is not an identifier"
        )
    if (
        result.source_clickhouse_host != result.target_clickhouse_host
        or result.source_clickhouse_native_port != result.target_clickhouse_native_port
        or result.source_clickhouse_http_port != result.target_clickhouse_http_port
        or result.source_clickhouse_hostname != result.write_clickhouse_hostname
    ):
        raise DeploymentValidationError(
            "source and isolated target must be two databases on the same DEV ClickHouse"
        )
    return result


def _common_service(config: DeploymentConfig, component: str) -> dict[str, Any]:
    return {
        "pull_policy": "never",
        "restart": "unless-stopped",
        "init": True,
        "user": f"{RUNTIME_UID}:{RUNTIME_GID}",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 256,
        "stop_grace_period": "180s",
        "networks": ["application-existing", "kafka-existing"],
        "logging": {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        },
        "labels": {
            "futureagi.environment": "development",
            "futureagi.change": "property-catalog",
            "futureagi.production-use": "forbidden",
            "futureagi.property-catalog-deployment": config.deployment_id,
            "futureagi.component": component,
            "futureagi.public-routing": "disabled",
            "futureagi.schedule": "disabled",
            "futureagi.read-mode": "off",
            "futureagi.property-catalog-runtime-profile": config.runtime_profile,
        },
    }


def _bind(source: Path, target: str, *, read_only: bool) -> dict[str, Any]:
    return {
        "type": "bind",
        "source": os.fspath(source),
        "target": target,
        "read_only": read_only,
        "bind": {"create_host_path": False},
    }


def _operator_environment(config: DeploymentConfig) -> dict[str, str]:
    cloud_deployment = "" if config.runtime_profile == "oss" else "DEV"
    return {
        "ENV_TYPE": "development",
        "CLOUD_DEPLOYMENT": cloud_deployment,
        "DJANGO_SETTINGS_MODULE": "tfc.settings.settings",
        "SERVICE_TYPE": "bootstrap",
        "STARTUP_DB_MUTATION_MODE": "operator",
        "NO_STARTUP_DB_MUTATIONS": "true",
        "FAST_STARTUP": "true",
        "SENTRY_ENABLED": "false",
        "OTEL_ENABLED": "false",
        "FUTURE_AGI_TELEMETRY_DISABLED": "true",
        "ENABLE_INTEGRATIONS": "false",
        "DJANGO_CACHE_BACKEND": "locmem",
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=100000",
        "CH_ENABLED": "false",
        "CH_DUAL_WRITE": "false",
        "CH25_HOST": config.source_clickhouse_host,
        "CH25_HTTP_PORT": str(config.source_clickhouse_http_port),
        "CH25_TCP_PORT": str(config.source_clickhouse_native_port),
        "CH25_DATABASE": config.source_database,
        "CH25_SERVER_ENFORCED_READONLY": "true",
        "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "off",
        "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": "false",
        "PROPERTY_CATALOG_READ_MODE": "off",
        "PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED": "false",
        "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": "false",
        "PROPERTY_CATALOG_DEV_ORGANIZATION_ID": config.organization_id,
        "PROPERTY_CATALOG_DEV_WORKSPACE_ID": config.workspace_id,
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": config.workspace_id,
        "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST": ",".join(config.project_ids),
        "PROPERTY_CATALOG_DEV_ENVIRONMENT": "development",
        "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": cloud_deployment,
        "PROPERTY_CATALOG_DEV_IDENTITY": config.dev_identity,
        "PROPERTY_CATALOG_DEV_SOURCE_DATABASE": config.source_database,
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": config.target_database,
        "PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT": ROLLOUT_ACK,
        "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY": RUNTIME_FACTORY,
        "PROPERTY_CATALOG_DEV_WRITE_CH_HOST": config.target_clickhouse_host,
        "PROPERTY_CATALOG_DEV_WRITE_CH_PORT": str(config.target_clickhouse_native_port),
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": config.target_database,
        "PROPERTY_CATALOG_DEV_CATALOG_EPOCH": str(config.epoch),
        "PROPERTY_CATALOG_DEV_PROJECTION_VERSION": str(config.projection_version),
        "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID": config.hot_producer_stream_id,
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": REVISION_FENCE_FILE,
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE": DRAIN_PROOF_FILE,
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE": PRODUCER_RETIREMENT_FILE,
        "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY": CONTAINER_SPOOL_DIRECTORY,
        "PROPERTY_CATALOG_DEV_SPAN_SINCE": config.span_since,
        "PROPERTY_CATALOG_DEV_SPAN_UNTIL": config.span_until,
        "PROPERTY_CATALOG_DEV_SIDECAR_ACK": SIDECAR_ACK,
        "PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME": (
            config.write_clickhouse_hostname
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME": (
            config.source_clickhouse_hostname
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE": config.postgres_database,
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_USER": config.postgres_user,
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS": (
            config.postgres_server_address
        ),
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT": str(
            config.postgres_server_port
        ),
        "PROPERTY_CATALOG_DEV_MAX_WALL_MS": "100000",
        "HOME": f"{CONTAINER_RUNTIME_DIRECTORY}/home",
        "XDG_CACHE_HOME": f"{CONTAINER_RUNTIME_DIRECTORY}/cache",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def render_compose(config: DeploymentConfig) -> dict[str, Any]:
    if not isinstance(config, DeploymentConfig):
        raise TypeError("config must be DeploymentConfig")
    private = config.private_directory

    producer = _common_service(config, "unrouted-hot-producer")
    producer.update(
        {
            "image": config.collector_image,
            "entrypoint": ["/usr/local/bin/fi-collector"],
            "command": ["--config", "/dev/null"],
            "env_file": [os.fspath(private / "producer.env")],
            "environment": {
                "FI_CH_URL": config.source_clickhouse_http_url,
                "FI_CH_DATABASE": config.source_database,
                "FI_GRPC_ADDR": "127.0.0.1:4317",
                "FI_HTTP_ADDR": "127.0.0.1:4318",
                "FI_DEAD_LETTER_FILE": (
                    f"{CONTAINER_RUNTIME_DIRECTORY}/span-dead-letter/dead_letter.jsonl"
                ),
                "FI_CATALOG_MODE": "disabled",
                "FI_PROPERTY_CATALOG_MODE": "kafka",
                "FI_PROPERTY_CATALOG_ENVIRONMENT": "development",
                "FI_PROPERTY_CATALOG_DEV_ACK": GO_DEV_ACK,
                "FI_PROPERTY_CATALOG_EPOCH": str(config.epoch),
                "FI_PROPERTY_CATALOG_PROJECTION_VERSION": str(
                    config.projection_version
                ),
                "FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID": (
                    config.hot_producer_stream_id
                ),
                "FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST": config.workspace_id,
                "FI_PROPERTY_CATALOG_REVISION_FENCE_FILE": REVISION_FENCE_FILE,
                "FI_PROPERTY_CATALOG_SPOOL_DIR": CONTAINER_SPOOL_DIRECTORY,
                "FI_PROPERTY_CATALOG_REPLAY_INTERVAL": "1s",
                "FI_PROPERTY_CATALOG_KAFKA_BROKERS": ",".join(config.kafka_brokers),
                "FI_PROPERTY_CATALOG_KAFKA_TOPIC": config.kafka_topic,
            },
            "volumes": [
                _bind(
                    config.runtime_directory,
                    CONTAINER_RUNTIME_DIRECTORY,
                    read_only=False,
                ),
            ],
            # The carrier image declares /var/lib/fi-collector as VOLUME.
            # Override it with tmpfs so Docker cannot create an anonymous
            # volume outside the one reviewed runtime bind.
            "tmpfs": [
                "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1770,uid=65532,gid=65532",
                "/var/lib/fi-collector:rw,noexec,nosuid,nodev,size=8m,"
                "mode=0770,uid=65532,gid=65532",
            ],
            "mem_limit": "2g",
            "cpus": "2.0",
        }
    )
    producer["labels"]["futureagi.collector-image-id"] = config.collector_image
    producer["labels"]["futureagi.otlp-listeners"] = "loopback-only"

    consumer = _common_service(config, "durable-consumer")
    consumer.update(
        {
            "image": config.collector_image,
            "entrypoint": ["/usr/local/bin/fi-property-catalog-consumer"],
            "command": ["--seed-from-delivery-ledger"],
            "env_file": [
                os.fspath(private / "consumer-write-clickhouse.env"),
                os.fspath(private / "consumer-ledger-clickhouse.env"),
            ],
            "environment": {
                "FI_PROPERTY_CATALOG_CONSUMER_MODE": "kafka",
                "FI_PROPERTY_CATALOG_ENVIRONMENT": "development",
                "FI_PROPERTY_CATALOG_DEV_ACK": GO_DEV_ACK,
                "FI_PROPERTY_CATALOG_CH_URL": config.target_clickhouse_http_url,
                "FI_PROPERTY_CATALOG_CH_DATABASE": config.target_database,
                "FI_PROPERTY_CATALOG_LEDGER_CH_URL": (
                    config.target_clickhouse_http_url
                ),
                "FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE": config.target_database,
                "FI_PROPERTY_CATALOG_KAFKA_BROKERS": ",".join(config.kafka_brokers),
                "FI_PROPERTY_CATALOG_KAFKA_TOPIC": config.kafka_topic,
                "FI_PROPERTY_CATALOG_KAFKA_CONSUMER_GROUP": (
                    config.kafka_consumer_group
                ),
            },
            "tmpfs": [
                "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1770,uid=65532,gid=65532",
                "/var/lib/fi-collector:rw,noexec,nosuid,nodev,size=8m,"
                "mode=0770,uid=65532,gid=65532",
            ],
            "mem_limit": "2g",
            "cpus": "2.0",
        }
    )
    consumer["labels"]["futureagi.collector-image-id"] = config.collector_image

    operator = _common_service(config, "one-shot-operator")
    operator.update(
        {
            "image": config.operator_image,
            "restart": "no",
            "profiles": ["operator"],
            "working_dir": "/app/backend",
            "entrypoint": [
                "python",
                "manage.py",
                "ch25_property_catalog_dev_rollout",
            ],
            # An empty command is the management command's zero-I/O dry run.
            # `docker compose run ... --status` and `... --execute` are explicit.
            "command": [],
            "env_file": [
                os.fspath(private / "operator-runtime.env"),
                os.fspath(private / "operator-postgres.env"),
                os.fspath(private / "operator-source-clickhouse.env"),
                os.fspath(private / "operator-target-clickhouse.env"),
            ],
            "environment": _operator_environment(config),
            "volumes": [
                _bind(
                    config.runtime_directory,
                    CONTAINER_RUNTIME_DIRECTORY,
                    read_only=False,
                )
            ],
            "tmpfs": [
                "/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1770,uid=65532,gid=65532",
                "/app/backend/logs:rw,noexec,nosuid,nodev,size=64m,mode=0770,uid=65532,gid=65532",
                "/app/backend/tfc/logs:rw,noexec,nosuid,nodev,size=64m,"
                "mode=0770,uid=65532,gid=65532",
            ],
            "mem_limit": "4g",
            "cpus": "4.0",
            "networks": ["application-existing"],
        }
    )

    compose = {
        "name": f"fi-property-catalog-{config.deployment_id}",
        "services": {
            "property-catalog-producer": producer,
            "property-catalog-consumer": consumer,
            "property-catalog-operator": operator,
        },
        "networks": {
            "application-existing": {
                "external": True,
                "name": config.application_docker_network,
            },
            "kafka-existing": {
                "external": True,
                "name": config.kafka_docker_network,
            },
        },
    }
    _validate_compose(compose, config)
    return copy.deepcopy(compose)


def _validate_compose(compose: Any, config: DeploymentConfig) -> None:
    top = _mapping(compose, "rendered Compose", ("name", "services", "networks"))
    expected_name = f"fi-property-catalog-{config.deployment_id}"
    if top["name"] != expected_name:
        raise DeploymentValidationError(
            "Compose project name escaped the deployment scope"
        )
    services = _mapping(
        top["services"],
        "rendered services",
        (
            "property-catalog-producer",
            "property-catalog-consumer",
            "property-catalog-operator",
        ),
    )
    networks = _mapping(
        top["networks"],
        "rendered networks",
        ("application-existing", "kafka-existing"),
    )
    if networks != {
        "application-existing": {
            "external": True,
            "name": config.application_docker_network,
        },
        "kafka-existing": {
            "external": True,
            "name": config.kafka_docker_network,
        },
    }:
        raise DeploymentValidationError(
            "renderer must attach, never create or alter, the DEV network"
        )

    for name, service in services.items():
        if not isinstance(service, Mapping):
            raise DeploymentValidationError(f"rendered service {name} is not a mapping")
        if any(
            forbidden in service
            for forbidden in (
                "build",
                "ports",
                "expose",
                "privileged",
                "network_mode",
                "depends_on",
                "links",
                "devices",
            )
        ):
            raise DeploymentValidationError(
                f"rendered service {name} gained routing, build, privilege, or coupling"
            )
        if (
            service.get("pull_policy") != "never"
            or service.get("read_only") is not True
            or service.get("user") != f"{RUNTIME_UID}:{RUNTIME_GID}"
            or service.get("cap_drop") != ["ALL"]
            or service.get("security_opt") != ["no-new-privileges:true"]
        ):
            raise DeploymentValidationError(
                f"rendered service {name} hardening drifted"
            )
        expected_networks = (
            ["application-existing"]
            if name == "property-catalog-operator"
            else ["application-existing", "kafka-existing"]
        )
        if service.get("networks") != expected_networks:
            raise DeploymentValidationError(
                f"rendered service {name} network isolation drifted"
            )
        labels = service.get("labels", {})
        if (
            labels.get("futureagi.environment") != "development"
            or labels.get("futureagi.production-use") != "forbidden"
            or labels.get("futureagi.public-routing") != "disabled"
            or labels.get("futureagi.schedule") != "disabled"
            or labels.get("futureagi.read-mode") != "off"
            or labels.get("futureagi.property-catalog-runtime-profile")
            != config.runtime_profile
        ):
            raise DeploymentValidationError(
                f"rendered service {name} safety labels drifted"
            )
        for volume in service.get("volumes", []):
            if volume.get("type") != "bind" or volume.get("bind") != {
                "create_host_path": False
            }:
                raise DeploymentValidationError(
                    "all binds must refuse implicit host paths"
                )
            source = Path(str(volume.get("source", "")))
            if source != config.root and config.root not in source.parents:
                raise DeploymentValidationError(
                    "a bind escaped the dedicated host root"
                )

    producer = services["property-catalog-producer"]
    consumer = services["property-catalog-consumer"]
    operator = services["property-catalog-operator"]
    if (
        producer.get("image") != config.collector_image
        or consumer.get("image") != config.collector_image
        or producer.get("labels", {}).get("futureagi.collector-image-id")
        != config.collector_image
        or consumer.get("labels", {}).get("futureagi.collector-image-id")
        != config.collector_image
    ):
        raise DeploymentValidationError(
            "producer and consumer must use the same exact reviewed collector image ID"
        )
    if producer.get("entrypoint") != ["/usr/local/bin/fi-collector"]:
        raise DeploymentValidationError("producer is not the packaged image binary")
    if producer.get("command") != ["--config", "/dev/null"]:
        raise DeploymentValidationError(
            "producer inherited an unreviewed YAML configuration"
        )
    producer_environment = producer.get("environment", {})
    if (
        producer_environment.get("FI_GRPC_ADDR") != "127.0.0.1:4317"
        or producer_environment.get("FI_HTTP_ADDR") != "127.0.0.1:4318"
        or producer_environment.get("FI_CATALOG_MODE") != "disabled"
        or producer_environment.get("FI_PROPERTY_CATALOG_MODE") != "kafka"
        or producer_environment.get("FI_PROPERTY_CATALOG_KAFKA_TOPIC")
        != config.kafka_topic
    ):
        raise DeploymentValidationError("producer routing or catalog mode drifted")
    if producer.get("env_file") != [
        os.fspath(config.private_directory / "producer.env")
    ]:
        raise DeploymentValidationError("producer credential file reference drifted")
    for service_name, service in (("producer", producer), ("consumer", consumer)):
        if not any(
            value.startswith("/var/lib/fi-collector:")
            for value in service.get("tmpfs", [])
        ):
            raise DeploymentValidationError(
                f"{service_name} would inherit an anonymous image volume"
            )

    if consumer.get("entrypoint") != [
        "/usr/local/bin/fi-property-catalog-consumer"
    ] or consumer.get("command") != ["--seed-from-delivery-ledger"]:
        raise DeploymentValidationError(
            "consumer packaged image binary or durable seed mode drifted"
        )
    consumer_environment = consumer.get("environment", {})
    if (
        consumer_environment.get("FI_PROPERTY_CATALOG_KAFKA_TOPIC")
        != config.kafka_topic
        or consumer_environment.get("FI_PROPERTY_CATALOG_KAFKA_CONSUMER_GROUP")
        != config.kafka_consumer_group
        or consumer_environment.get("FI_PROPERTY_CATALOG_CH_DATABASE")
        != config.target_database
        or consumer_environment.get("FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE")
        != config.target_database
    ):
        raise DeploymentValidationError("consumer target/topic/group drifted")
    if consumer.get("env_file") != [
        os.fspath(config.private_directory / "consumer-write-clickhouse.env"),
        os.fspath(config.private_directory / "consumer-ledger-clickhouse.env"),
    ]:
        raise DeploymentValidationError("consumer credential file references drifted")

    expected_operator_entrypoint = [
        "python",
        "manage.py",
        "ch25_property_catalog_dev_rollout",
    ]
    operator_environment = operator.get("environment", {})
    expected_cloud_deployment = "" if config.runtime_profile == "oss" else "DEV"
    if (
        operator.get("profiles") != ["operator"]
        or operator.get("restart") != "no"
        or operator.get("entrypoint") != expected_operator_entrypoint
        or operator.get("command") != []
        or operator_environment.get("SERVICE_TYPE") != "bootstrap"
        or operator_environment.get("STARTUP_DB_MUTATION_MODE") != "operator"
        or operator_environment.get("NO_STARTUP_DB_MUTATIONS") != "true"
        or operator_environment.get("SENTRY_ENABLED") != "false"
        or operator_environment.get("OTEL_ENABLED") != "false"
        or operator_environment.get("FUTURE_AGI_TELEMETRY_DISABLED") != "true"
        or operator_environment.get("CLOUD_DEPLOYMENT") != expected_cloud_deployment
        or operator_environment.get("PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT")
        != expected_cloud_deployment
        or operator_environment.get("PROPERTY_CATALOG_READ_MODE") != "off"
        or operator_environment.get("PROPERTY_CATALOG_DEV_RECONCILE_ENABLED") != "false"
        or operator_environment.get("PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED")
        != "false"
    ):
        raise DeploymentValidationError("one-shot operator safety contract drifted")
    if operator.get("env_file") != [
        os.fspath(config.private_directory / "operator-runtime.env"),
        os.fspath(config.private_directory / "operator-postgres.env"),
        os.fspath(config.private_directory / "operator-source-clickhouse.env"),
        os.fspath(config.private_directory / "operator-target-clickhouse.env"),
    ]:
        raise DeploymentValidationError("operator credential file references drifted")

    secret_keys = {key for values in _ENV_FILE_KEYS.values() for key in values}
    for name, service in services.items():
        inline = set(service.get("environment", {}))
        overlap = inline & secret_keys
        if overlap:
            raise DeploymentValidationError(
                f"rendered service {name} inlined credential keys {sorted(overlap)}"
            )

    expected_runtime_bind = _bind(
        config.runtime_directory,
        CONTAINER_RUNTIME_DIRECTORY,
        read_only=False,
    )
    if (
        producer.get("volumes") != [expected_runtime_bind]
        or operator.get("volumes") != [expected_runtime_bind]
        or consumer.get("volumes", []) != []
    ):
        raise DeploymentValidationError(
            "only the producer/operator shared runtime bind is permitted"
        )


def render_yaml(config: DeploymentConfig) -> str:
    compose = render_compose(config)
    rendered = yaml.safe_dump(compose, sort_keys=False, explicit_start=True)
    round_trip = yaml.load(rendered, Loader=_UniqueSafeLoader)
    _validate_compose(round_trip, config)
    if round_trip != compose:
        raise DeploymentValidationError("Compose YAML did not round-trip exactly")
    return rendered


def _regular_physical_file(path: Path, *, mode: int, owner: int) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DeploymentValidationError(
            f"required host file is unavailable: {path}"
        ) from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise DeploymentValidationError(
            f"host file must be a physical regular file: {path}"
        )
    if stat.S_IMODE(info.st_mode) != mode or info.st_uid != owner:
        raise DeploymentValidationError(
            f"host file must be owner={owner} mode={mode:04o}: {path}"
        )
    return info


def _physical_directory(
    path: Path, *, mode: int, owner: int, group: int | None = None
) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DeploymentValidationError(
            f"required host directory is unavailable: {path}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise DeploymentValidationError(
            f"host path must be a physical directory: {path}"
        )
    if stat.S_IMODE(info.st_mode) != mode or info.st_uid != owner:
        raise DeploymentValidationError(
            f"host directory must be owner={owner} mode={mode:04o}: {path}"
        )
    if group is not None and info.st_gid != group:
        raise DeploymentValidationError(f"host directory must be group={group}: {path}")
    return info


def _parse_env_file(
    path: Path, expected_keys: Sequence[str], owner: int
) -> dict[str, str]:
    _regular_physical_file(path, mode=0o600, owner=owner)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DeploymentValidationError(
            f"private env file is not UTF-8: {path}"
        ) from exc
    if "\r" in raw or "\x00" in raw or not raw.endswith("\n"):
        raise DeploymentValidationError(f"private env file is not canonical: {path}")
    values: dict[str, str] = {}
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DeploymentValidationError(
                f"private env file line {line_number} is not KEY=value: {path}"
            )
        key, value = line.split("=", 1)
        if _ENV_NAME_RE.fullmatch(key) is None or key in values:
            raise DeploymentValidationError(
                f"private env file has an invalid or duplicate key at line {line_number}: {path}"
            )
        if (
            value.strip() != value
            or "$" in value
            or _PLACEHOLDER_RE.search(value)
            or (not value and key not in _EMPTY_ALLOWED_KEYS)
        ):
            raise DeploymentValidationError(
                f"private env file has an unsafe value at line {line_number}: {path}"
            )
        values[key] = value
    if set(values) != set(expected_keys):
        raise DeploymentValidationError(
            f"private env file keys differ from its exact purpose: {path}"
        )
    return values


def validate_host(config: DeploymentConfig, *, owner_uid: int | None = None) -> None:
    """Validate host artifacts without invoking a process or network client."""

    if owner_uid is None:
        owner_uid = os.geteuid()
    _physical_directory(config.root, mode=0o700, owner=owner_uid)
    _physical_directory(config.private_directory, mode=0o700, owner=owner_uid)
    _physical_directory(
        config.runtime_directory,
        mode=0o770,
        owner=owner_uid,
        group=RUNTIME_GID,
    )
    for child in ("cache", "home", "span-dead-letter"):
        _physical_directory(
            config.runtime_directory / child,
            mode=0o770,
            owner=owner_uid,
            group=RUNTIME_GID,
        )
    _physical_directory(
        config.runtime_directory / "catalog-spool",
        mode=0o700,
        owner=RUNTIME_UID,
        group=RUNTIME_GID,
    )

    envs = {
        filename: _parse_env_file(
            config.private_directory / filename, expected, owner_uid
        )
        for filename, expected in _ENV_FILE_KEYS.items()
    }

    # Compare identities only; never return or print values.
    if (
        envs["producer.env"]["FI_CH_USERNAME"]
        != envs["operator-source-clickhouse.env"]["CH25_USER"]
        or envs["producer.env"]["FI_CH_PASSWORD"]
        != envs["operator-source-clickhouse.env"]["CH25_PASSWORD"]
    ):
        raise DeploymentValidationError(
            "producer and operator source ClickHouse credentials differ"
        )
    target_users = {
        envs["operator-target-clickhouse.env"]["PROPERTY_CATALOG_DEV_WRITE_CH_USER"],
        envs["consumer-write-clickhouse.env"]["FI_PROPERTY_CATALOG_CH_USERNAME"],
        envs["consumer-ledger-clickhouse.env"][
            "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"
        ],
    }
    source_user = envs["operator-source-clickhouse.env"]["CH25_USER"]
    if len(target_users) != 3 or source_user in target_users:
        raise DeploymentValidationError(
            "source, control writer, consumer writer, and ledger reader require distinct roles"
        )
    if (
        envs["operator-postgres.env"]["PG_DB"] != config.postgres_database
        or envs["operator-postgres.env"]["PG_USER"] != config.postgres_user
    ):
        raise DeploymentValidationError(
            "operator PostgreSQL identity differs from frozen provenance"
        )
    pg_dsn = envs["producer.env"]["FI_PG_WRITE"]
    operator_pg = envs["operator-postgres.env"]
    pg_host = operator_pg["PGBOUNCER_HOST"]
    pg_port_text = operator_pg["PGBOUNCER_PORT"]
    if (
        _SAFE_NAME_RE.fullmatch(pg_host) is None
        or _PRODUCTION_TOKEN_RE.search(pg_host)
        or not pg_port_text.isascii()
        or not pg_port_text.isdigit()
        or not 1 <= int(pg_port_text) <= 65_535
    ):
        raise DeploymentValidationError("operator PgBouncer route is invalid")
    try:
        parsed_pg_dsn = urlsplit(pg_dsn)
        parsed_pg_port = parsed_pg_dsn.port
    except ValueError as exc:
        raise DeploymentValidationError("producer PostgreSQL DSN is invalid") from exc
    if (
        parsed_pg_dsn.scheme not in {"postgres", "postgresql"}
        or parsed_pg_dsn.hostname != operator_pg["PGBOUNCER_HOST"]
        or parsed_pg_port != int(operator_pg["PGBOUNCER_PORT"])
        or unquote(parsed_pg_dsn.username or "") != config.postgres_user
        or unquote(parsed_pg_dsn.password or "") != operator_pg["PG_PASSWORD"]
        or unquote(parsed_pg_dsn.path.removeprefix("/")) != config.postgres_database
        or parsed_pg_dsn.fragment
    ):
        raise DeploymentValidationError(
            "producer PostgreSQL DSN differs from the reviewed DEV PgBouncer identity"
        )


def validate_rendered(path: os.PathLike[str] | str, config: DeploymentConfig) -> None:
    try:
        raw = yaml.load(
            Path(path).read_text(encoding="utf-8"), Loader=_UniqueSafeLoader
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DeploymentValidationError(f"cannot read rendered Compose: {exc}") from exc
    _validate_compose(raw, config)
    if raw != render_compose(config):
        raise DeploymentValidationError("rendered Compose differs from reviewed output")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render/validate the isolated Docker-host property-catalog DEV workload"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--output")
    parser.add_argument("--validate-rendered")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--host-preflight", action="store_true")
    args = parser.parse_args(argv)
    if args.output and args.validate_only:
        parser.error("--output and --validate-only are mutually exclusive")
    if args.output and args.validate_rendered:
        parser.error("--output and --validate-rendered are mutually exclusive")
    try:
        config = load_config(args.config)
        rendered = render_yaml(config)
        if args.host_preflight:
            validate_host(config)
        if args.validate_rendered:
            validate_rendered(args.validate_rendered, config)
        if args.output:
            _write_atomic(Path(args.output), rendered)
        elif not args.validate_only and not args.validate_rendered:
            sys.stdout.write(rendered)
    except (DeploymentValidationError, OSError) as exc:
        sys.stderr.write(f"property-catalog Docker DEV workload rejected: {exc}\n")
        return 2
    if args.validate_only or args.validate_rendered:
        sys.stdout.write(
            f"validated {config.deployment_id}: 2 long-running services, "
            "1 dry-run operator profile, no published ports\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
