#!/usr/bin/env python3
"""Render the one-workspace unified property-catalog DEV workload.

The renderer validates every operator-controlled value before serializing any
Kubernetes object.  It intentionally emits Secret references, never Secrets or
secret values, and keeps periodic reconciliation disabled unless the operator
provides an activation digest from a completed bootstrap.
"""

from __future__ import annotations

import argparse
import copy
import ipaddress
import os
import re
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

FORMAT = "futureagi.property-catalog-dev-workload"
VERSION = 1
WORKLOAD_NAME = "property-catalog-dev"
CONSUMER_NAME = "property-catalog-dev-consumer"
SERVICE_NAME = "property-catalog-dev-otlp-canary"
CONFIG_MAP_NAME = "property-catalog-dev-config"
SERVICE_ACCOUNT_NAME = "property-catalog-dev"
PVC_NAME = "property-catalog-dev-runtime"
TASK_QUEUE = "property_catalog_dev_sidecar"
RUNTIME_DIRECTORY = "/var/lib/property-catalog-runtime"
REVISION_FENCE_FILE = f"{RUNTIME_DIRECTORY}/revision-fence-v2.json"
DRAIN_PROOF_FILE = f"{RUNTIME_DIRECTORY}/producer-drain-proof-v2.json"
PRODUCER_RETIREMENT_FILE = f"{RUNTIME_DIRECTORY}/producer-state-retirements-v1.json"
ROLLOUT_ACK = "FI_PROPERTY_CATALOG_DEV"
SIDECAR_ACK = "FI_PROPERTY_CATALOG_PYTHON_GO_SIDECAR_V1"
GO_DEV_ACK = "FI_PROPERTY_CATALOG_V1_DEV_ONLY"
RUNTIME_FACTORY = (
    "tracer.services.clickhouse.v2.property_catalog.dev_runtime."
    "configured_property_catalog_dev_runtime"
)
SCHEDULE_ID_PREFIX = "unified-property-catalog-dev"
TERMINATION_GRACE_SECONDS = 180
RUNTIME_UID = 65_532
_RUNTIME_INIT_SCRIPT = """\
import os
import stat
from pathlib import Path

root = Path("/var/lib/property-catalog-runtime")
paths = (root, root / "cache", root / "home", root / "span-dead-letter")
for path in paths:
    path.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise RuntimeError(f"runtime path is not a physical directory: {path}")
    os.chown(path, 65532, 65532, follow_symlinks=False)
    os.chmod(path, 0o770)
"""

_TOP_LEVEL_FIELDS = (
    "format",
    "version",
    "namespace",
    "images",
    "workspaces",
    "catalog",
    "runtime",
    "infrastructure",
    "provenance",
    "storage",
    "secrets",
)
_IMAGE_FIELDS = ("backend", "collector", "consumer")
_WORKSPACE_FIELDS = ("organization_id", "workspace_id", "project_ids")
_CATALOG_FIELDS = (
    "epoch",
    "projection_version",
    "hot_producer_stream_id",
    "source_database",
    "target_database",
    "span_since",
    "span_until",
    "otlp_traffic_authorized",
    "dev_identity",
)
_RUNTIME_FIELDS = (
    "directory",
    "revision_fence_file",
    "drain_proof_file",
    "producer_retirement_file",
)
_INFRASTRUCTURE_FIELDS = (
    "temporal_host",
    "temporal_namespace",
    "source_clickhouse_host",
    "source_clickhouse_native_port",
    "source_clickhouse_http_url",
    "target_clickhouse_host",
    "target_clickhouse_native_port",
    "target_clickhouse_http_url",
    "kafka_brokers",
    "kafka_topic",
    "kafka_consumer_group",
)
_PROVENANCE_FIELDS = (
    "write_clickhouse_hostname",
    "source_clickhouse_hostname",
    "postgres_database",
    "postgres_user",
    "postgres_server_address",
    "postgres_server_port",
)
_STORAGE_FIELDS = ("storage_class", "size")
_SECRET_FIELDS = (
    "backend_env",
    "collector_env",
    "source_read_clickhouse",
    "control_write_clickhouse",
    "consumer_write_clickhouse",
    "consumer_ledger_clickhouse",
    "image_pull",
)

_SAFE_NAME_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CATALOG_DATABASE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_RESERVED_CATALOG_DATABASES = frozenset(
    {"default", "futureagi", "information_schema", "property_catalog", "system"}
)
_DEV_IDENTITY_RE = re.compile(r"^dev:[a-z0-9][a-z0-9._:/-]{2,127}$")
_KAFKA_IDENTITY_RE = re.compile(r"^[a-zA-Z0-9._-]{1,249}$")
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@[s]ha256:(?P<digest>[0-9a-f]{64})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HOST_PORT_RE = re.compile(
    r"^(?P<host>[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?):(?P<port>[0-9]{1,5})$"
)
_PLACEHOLDER_RE = re.compile(
    r"(?:replace|placeholder|change[-_ ]?me|todo|example|<[^>]*>)",
    re.IGNORECASE,
)
_PRODUCTION_TOKEN_RE = re.compile(
    r"(?:^|[-._/:])(prod|production|live)(?:$|[-._/:])",
    re.IGNORECASE,
)


class WorkloadValidationError(ValueError):
    """The requested manifest is not provably isolated to the DEV contract."""


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
            raise WorkloadValidationError(
                "configuration mapping keys must be scalar"
            ) from exc
        if duplicate:
            raise WorkloadValidationError(
                f"configuration contains duplicate key {key!r}"
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class WorkloadConfig:
    namespace: str
    backend_image: str
    collector_image: str
    consumer_image: str
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
    temporal_host: str
    temporal_namespace: str
    source_clickhouse_host: str
    source_clickhouse_native_port: int
    source_clickhouse_http_url: str
    target_clickhouse_host: str
    target_clickhouse_native_port: int
    target_clickhouse_http_url: str
    kafka_brokers: tuple[str, ...]
    kafka_topic: str
    kafka_consumer_group: str
    write_clickhouse_hostname: str
    source_clickhouse_hostname: str
    postgres_database: str
    postgres_user: str
    postgres_server_address: str
    postgres_server_port: int
    storage_class: str
    storage_size: str
    backend_env_secret: str
    collector_env_secret: str
    source_read_clickhouse_secret: str
    control_write_clickhouse_secret: str
    consumer_write_clickhouse_secret: str
    consumer_ledger_clickhouse_secret: str
    image_pull_secret: str


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise WorkloadValidationError(f"{label} must be a string-keyed mapping")
    return value


def _exact_fields(value: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    if tuple(value) != tuple(fields):
        missing = sorted(set(fields) - set(value))
        extra = sorted(set(value) - set(fields))
        raise WorkloadValidationError(
            f"{label} fields/order are not canonical; missing={missing}, extra={extra}"
        )


def _text(value: Any, label: str, *, reject_production: bool = True) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise WorkloadValidationError(f"{label} must be non-empty unpadded text")
    if _PLACEHOLDER_RE.search(value):
        raise WorkloadValidationError(f"{label} contains a placeholder")
    if reject_production and _PRODUCTION_TOKEN_RE.search(value):
        raise WorkloadValidationError(f"{label} contains a production/live token")
    return value


def _safe_name(value: Any, label: str) -> str:
    result = _text(value, label)
    if _SAFE_NAME_RE.fullmatch(result) is None:
        raise WorkloadValidationError(f"{label} is not a safe Kubernetes name")
    return result


def _require_dev_token(value: str, label: str) -> str:
    if re.search(r"(?:^|[-._])dev(?:$|[-._])", value) is None:
        raise WorkloadValidationError(f"{label} must contain the exact token 'dev'")
    return value


def _uuid(value: Any, label: str) -> str:
    result = _text(value, label)
    try:
        parsed = uuid.UUID(result)
    except (ValueError, AttributeError) as exc:
        raise WorkloadValidationError(f"{label} must be a canonical UUID") from exc
    if str(parsed) != result or parsed.variant != uuid.RFC_4122 or parsed.version != 4:
        raise WorkloadValidationError(f"{label} must be a canonical RFC-4122 UUIDv4")
    return result


def _positive_uint16(value: Any, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 65_535:
        raise WorkloadValidationError(f"{label} must be a positive UInt16")
    return value


def _port(value: Any, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 65_535:
        raise WorkloadValidationError(f"{label} must be a TCP port")
    return value


def _image(value: Any, label: str) -> str:
    result = _text(value, label)
    matched = _IMAGE_RE.fullmatch(result)
    if matched is None or ":latest" in result:
        raise WorkloadValidationError(
            f"{label} must be a lowercase digest-pinned image reference"
        )
    digest = matched.group("digest")
    if len(set(digest)) < 4:
        raise WorkloadValidationError(f"{label} contains a placeholder image digest")
    return result


def _host_port(value: Any, label: str) -> str:
    result = _text(value, label)
    matched = _HOST_PORT_RE.fullmatch(result)
    if matched is None:
        raise WorkloadValidationError(f"{label} must be one DNS host:port")
    _port(int(matched.group("port")), label)
    return result


def _http_url(value: Any, label: str) -> str:
    result = _text(value, label)
    parsed = urlsplit(result)
    try:
        port = parsed.port
    except ValueError as exc:
        raise WorkloadValidationError(f"{label} contains an invalid port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or port is None
    ):
        raise WorkloadValidationError(
            f"{label} must be one credential-free HTTP(S) origin with an explicit port"
        )
    _text(parsed.hostname, f"{label} host")
    _port(port, f"{label} port")
    return result.rstrip("/")


def _server_identity(value: Any, label: str) -> str:
    result = _text(value, label)
    if (
        len(result.encode("utf-8")) > 255
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", result) is None
    ):
        raise WorkloadValidationError(
            f"{label} must be one exact case-sensitive server identity"
        )
    return result


def _postgres_identity(value: Any, label: str) -> str:
    result = _text(value, label)
    if (
        len(result.encode("utf-8")) > 63
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", result) is None
    ):
        raise WorkloadValidationError(f"{label} is not a reviewed PostgreSQL identity")
    return result


def _canonical_ip(value: Any, label: str) -> str:
    result = _text(value, label)
    try:
        parsed = ipaddress.ip_address(result)
    except ValueError as exc:
        raise WorkloadValidationError(
            f"{label} must be a canonical literal IP address"
        ) from exc
    if str(parsed) != result:
        raise WorkloadValidationError(f"{label} must be a canonical literal IP address")
    return result


def _utc_hour(value: Any, label: str) -> tuple[str, datetime]:
    result = _text(value, label, reject_production=False)
    try:
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise WorkloadValidationError(
            f"{label} must be a canonical UTC whole hour (YYYY-MM-DDTHH:00:00Z)"
        ) from exc
    if parsed.minute or parsed.second or parsed.microsecond:
        raise WorkloadValidationError(f"{label} must be a UTC whole hour")
    return result, parsed


def _load_raw(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkloadValidationError(f"cannot read configuration: {exc}") from exc
    if len(raw.encode("utf-8")) > 64 * 1024:
        raise WorkloadValidationError("configuration exceeds 64 KiB")
    try:
        decoded = yaml.load(raw, Loader=_UniqueSafeLoader)
    except (yaml.YAMLError, WorkloadValidationError) as exc:
        raise WorkloadValidationError(f"configuration YAML is invalid: {exc}") from exc
    return _mapping(decoded, "configuration")


def validate_config(raw: Mapping[str, Any]) -> WorkloadConfig:
    """Return a normalized config only after the complete DEV proof passes."""

    raw = _mapping(raw, "configuration")
    _exact_fields(raw, _TOP_LEVEL_FIELDS, "configuration")
    if raw["format"] != FORMAT or raw["version"] != VERSION:
        raise WorkloadValidationError("configuration format/version is not v1")
    namespace = _require_dev_token(
        _safe_name(raw["namespace"], "namespace"), "namespace"
    )

    images = _mapping(raw["images"], "images")
    _exact_fields(images, _IMAGE_FIELDS, "images")
    backend_image = _image(images["backend"], "backend image")
    collector_image = _image(images["collector"], "collector image")
    consumer_image = _image(images["consumer"], "consumer image")
    if consumer_image != collector_image:
        raise WorkloadValidationError(
            "consumer image must exactly equal the live collector candidate image"
        )

    workspaces = raw["workspaces"]
    if not isinstance(workspaces, list) or len(workspaces) != 1:
        raise WorkloadValidationError("exactly one workspace must be configured")
    workspace = _mapping(workspaces[0], "workspaces[0]")
    _exact_fields(workspace, _WORKSPACE_FIELDS, "workspaces[0]")
    organization_id = _uuid(workspace["organization_id"], "organization_id")
    workspace_id = _uuid(workspace["workspace_id"], "workspace_id")
    raw_projects = workspace["project_ids"]
    if not isinstance(raw_projects, list) or not 1 <= len(raw_projects) <= 256:
        raise WorkloadValidationError("project_ids must contain 1..256 UUIDs")
    project_ids = tuple(_uuid(value, "project_id") for value in raw_projects)
    if project_ids != tuple(sorted(set(project_ids))):
        raise WorkloadValidationError("project_ids must be sorted and unique")

    catalog = _mapping(raw["catalog"], "catalog")
    _exact_fields(catalog, _CATALOG_FIELDS, "catalog")
    epoch = _positive_uint16(catalog["epoch"], "catalog epoch")
    projection_version = _positive_uint16(
        catalog["projection_version"], "projection version"
    )
    hot_producer_stream_id = _uuid(
        catalog["hot_producer_stream_id"], "hot_producer_stream_id"
    )
    source_database = _text(catalog["source_database"], "source database")
    if _SAFE_IDENTIFIER_RE.fullmatch(source_database) is None:
        raise WorkloadValidationError("source database is not a safe identifier")
    target_database = _text(catalog["target_database"], "target database")
    if (
        _CATALOG_DATABASE_RE.fullmatch(target_database) is None
        or target_database in _RESERVED_CATALOG_DATABASES
    ):
        raise WorkloadValidationError(
            "target database must be a safe lowercase ClickHouse identifier "
            "isolated from production and source databases"
        )
    if source_database == target_database:
        raise WorkloadValidationError("source and target databases must differ")
    span_since, since = _utc_hour(catalog["span_since"], "span_since")
    span_until, until = _utc_hour(catalog["span_until"], "span_until")
    if not since < until or until - since > timedelta(days=366):
        raise WorkloadValidationError(
            "span window must be non-empty and no longer than 366 days"
        )
    if catalog["otlp_traffic_authorized"] is not False:
        raise WorkloadValidationError(
            "this bootstrap phase requires otlp_traffic_authorized=false"
        )
    dev_identity = _text(catalog["dev_identity"], "dev identity")
    if (
        _DEV_IDENTITY_RE.fullmatch(dev_identity) is None
        or "prod" in dev_identity.casefold()
        or "live" in dev_identity.casefold()
    ):
        raise WorkloadValidationError("dev identity does not match the reviewed form")

    runtime = _mapping(raw["runtime"], "runtime")
    _exact_fields(runtime, _RUNTIME_FIELDS, "runtime")
    if runtime["directory"] != RUNTIME_DIRECTORY:
        raise WorkloadValidationError(
            "runtime directory differs from the reviewed path"
        )
    if runtime["revision_fence_file"] != REVISION_FENCE_FILE:
        raise WorkloadValidationError(
            "revision fence differs from the reviewed shared path"
        )
    if runtime["drain_proof_file"] != DRAIN_PROOF_FILE:
        raise WorkloadValidationError(
            "drain proof differs from the reviewed fixed v2 path"
        )
    if runtime["producer_retirement_file"] != PRODUCER_RETIREMENT_FILE:
        raise WorkloadValidationError(
            "producer retirement proof differs from the reviewed fixed v1 path"
        )

    infrastructure = _mapping(raw["infrastructure"], "infrastructure")
    _exact_fields(infrastructure, _INFRASTRUCTURE_FIELDS, "infrastructure")
    temporal_host = _host_port(infrastructure["temporal_host"], "temporal host")
    temporal_namespace = _safe_name(
        infrastructure["temporal_namespace"], "temporal namespace"
    )
    source_clickhouse_host = _safe_name(
        infrastructure["source_clickhouse_host"], "source ClickHouse host"
    )
    source_clickhouse_native_port = _port(
        infrastructure["source_clickhouse_native_port"],
        "source ClickHouse native port",
    )
    source_clickhouse_http_url = _http_url(
        infrastructure["source_clickhouse_http_url"], "source ClickHouse HTTP URL"
    )
    if urlsplit(source_clickhouse_http_url).hostname != source_clickhouse_host:
        raise WorkloadValidationError(
            "source ClickHouse native and HTTP endpoints must use the same host"
        )
    target_clickhouse_host = _safe_name(
        infrastructure["target_clickhouse_host"], "target ClickHouse host"
    )
    target_clickhouse_native_port = _port(
        infrastructure["target_clickhouse_native_port"],
        "target ClickHouse native port",
    )
    target_clickhouse_http_url = _http_url(
        infrastructure["target_clickhouse_http_url"], "target ClickHouse HTTP URL"
    )
    if urlsplit(target_clickhouse_http_url).hostname != target_clickhouse_host:
        raise WorkloadValidationError(
            "target ClickHouse native and HTTP endpoints must use the same host"
        )
    raw_brokers = infrastructure["kafka_brokers"]
    if not isinstance(raw_brokers, list) or not 1 <= len(raw_brokers) <= 16:
        raise WorkloadValidationError("kafka_brokers must contain 1..16 entries")
    kafka_brokers = tuple(_host_port(value, "Kafka broker") for value in raw_brokers)
    if kafka_brokers != tuple(sorted(set(kafka_brokers))):
        raise WorkloadValidationError("Kafka brokers must be sorted and unique")
    kafka_topic = _text(infrastructure["kafka_topic"], "Kafka topic")
    kafka_consumer_group = _text(
        infrastructure["kafka_consumer_group"], "Kafka consumer group"
    )
    for value, label in (
        (kafka_topic, "Kafka topic"),
        (kafka_consumer_group, "Kafka consumer group"),
    ):
        if _KAFKA_IDENTITY_RE.fullmatch(value) is None:
            raise WorkloadValidationError(f"{label} is invalid")
        if "property-catalog" not in value or not re.search(
            r"(?:^|[._-])dev(?:$|[._-])", value
        ):
            raise WorkloadValidationError(
                f"{label} must identify the dedicated DEV property catalog"
            )
        if "span-attribute-catalog" in value:
            raise WorkloadValidationError(
                f"{label} must not reuse the retired topic/group"
            )

    provenance = _mapping(raw["provenance"], "provenance")
    _exact_fields(provenance, _PROVENANCE_FIELDS, "provenance")
    write_clickhouse_hostname = _server_identity(
        provenance["write_clickhouse_hostname"], "write ClickHouse hostname"
    )
    source_clickhouse_hostname = _server_identity(
        provenance["source_clickhouse_hostname"], "source ClickHouse hostname"
    )
    postgres_database = _postgres_identity(
        provenance["postgres_database"], "PostgreSQL database"
    )
    postgres_user = _postgres_identity(provenance["postgres_user"], "PostgreSQL user")
    postgres_server_address = _canonical_ip(
        provenance["postgres_server_address"], "PostgreSQL server address"
    )
    postgres_server_port = _port(
        provenance["postgres_server_port"], "PostgreSQL server port"
    )

    storage = _mapping(raw["storage"], "storage")
    _exact_fields(storage, _STORAGE_FIELDS, "storage")
    storage_class = _require_dev_token(
        _safe_name(storage["storage_class"], "storage class"), "storage class"
    )
    storage_tokens = set(re.split(r"[-._]", storage_class))
    if not {"posix", "rwo"}.issubset(storage_tokens):
        raise WorkloadValidationError(
            "storage class must identify a reviewed DEV RWO POSIX filesystem"
        )
    storage_size = _text(storage["size"], "storage size")
    if re.fullmatch(r"[1-9][0-9]*(?:Gi|Ti)", storage_size) is None:
        raise WorkloadValidationError("storage size must be a positive Gi/Ti quantity")

    secrets = _mapping(raw["secrets"], "secrets")
    _exact_fields(secrets, _SECRET_FIELDS, "secrets")
    normalized_secrets: dict[str, str] = {}
    for key in _SECRET_FIELDS:
        name = _require_dev_token(
            _safe_name(secrets[key], f"{key} secret"), f"{key} secret"
        )
        if "property-catalog" not in name:
            raise WorkloadValidationError(
                f"{key} secret must be purpose-built for property-catalog"
            )
        normalized_secrets[key] = name
    if len(set(normalized_secrets.values())) != len(normalized_secrets):
        raise WorkloadValidationError(
            "every workload function requires a distinct least-privilege Secret"
        )
    clickhouse_secrets = (
        normalized_secrets["source_read_clickhouse"],
        normalized_secrets["control_write_clickhouse"],
        normalized_secrets["consumer_write_clickhouse"],
        normalized_secrets["consumer_ledger_clickhouse"],
    )
    if len(set(clickhouse_secrets)) != len(clickhouse_secrets):
        raise WorkloadValidationError(
            "source/control/consumer writer/ledger identities require distinct Secrets"
        )

    return WorkloadConfig(
        namespace=namespace,
        backend_image=backend_image,
        collector_image=collector_image,
        consumer_image=consumer_image,
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_ids=project_ids,
        epoch=epoch,
        projection_version=projection_version,
        hot_producer_stream_id=hot_producer_stream_id,
        source_database=source_database,
        target_database=target_database,
        span_since=span_since,
        span_until=span_until,
        dev_identity=dev_identity,
        temporal_host=temporal_host,
        temporal_namespace=temporal_namespace,
        source_clickhouse_host=source_clickhouse_host,
        source_clickhouse_native_port=source_clickhouse_native_port,
        source_clickhouse_http_url=source_clickhouse_http_url,
        target_clickhouse_host=target_clickhouse_host,
        target_clickhouse_native_port=target_clickhouse_native_port,
        target_clickhouse_http_url=target_clickhouse_http_url,
        kafka_brokers=kafka_brokers,
        kafka_topic=kafka_topic,
        kafka_consumer_group=kafka_consumer_group,
        write_clickhouse_hostname=write_clickhouse_hostname,
        source_clickhouse_hostname=source_clickhouse_hostname,
        postgres_database=postgres_database,
        postgres_user=postgres_user,
        postgres_server_address=postgres_server_address,
        postgres_server_port=postgres_server_port,
        storage_class=storage_class,
        storage_size=storage_size,
        backend_env_secret=normalized_secrets["backend_env"],
        collector_env_secret=normalized_secrets["collector_env"],
        source_read_clickhouse_secret=normalized_secrets["source_read_clickhouse"],
        control_write_clickhouse_secret=normalized_secrets["control_write_clickhouse"],
        consumer_write_clickhouse_secret=normalized_secrets[
            "consumer_write_clickhouse"
        ],
        consumer_ledger_clickhouse_secret=normalized_secrets[
            "consumer_ledger_clickhouse"
        ],
        image_pull_secret=normalized_secrets["image_pull"],
    )


def load_config(path: Path) -> WorkloadConfig:
    return validate_config(_load_raw(path))


def _value_env(name: str, value: str | int) -> dict[str, Any]:
    return {"name": name, "value": str(value)}


def _secret_env(name: str, secret: str, key: str) -> dict[str, Any]:
    return {
        "name": name,
        "valueFrom": {"secretKeyRef": {"name": secret, "key": key}},
    }


def _metadata(
    name: str,
    config: WorkloadConfig,
    *,
    component: str,
    annotations: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "namespace": config.namespace,
        "labels": {
            "app.kubernetes.io/name": WORKLOAD_NAME,
            "app.kubernetes.io/component": component,
            "app.kubernetes.io/part-of": "unified-property-catalog",
            "futureagi.com/environment": "development",
            "futureagi.com/workspace-id": config.workspace_id,
        },
    }
    if annotations:
        result["annotations"] = dict(annotations)
    return result


def _config_map_data(
    config: WorkloadConfig, bootstrap_activation_sha256: str | None
) -> dict[str, str]:
    schedule_enabled = bootstrap_activation_sha256 is not None
    data = {
        "ENV_TYPE": "development",
        "CLOUD_DEPLOYMENT": "DEV",
        "DJANGO_SETTINGS_MODULE": "tfc.settings.settings",
        "NO_STARTUP_DB_MUTATIONS": "true",
        "FAST_STARTUP": "true",
        "CH25_HOST": config.source_clickhouse_host,
        "CH25_TCP_PORT": str(config.source_clickhouse_native_port),
        "CH25_DATABASE": config.source_database,
        "CH25_SERVER_ENFORCED_READONLY": "true",
        "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "off",
        "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": "false",
        "PROPERTY_CATALOG_READ_MODE": "off",
        "PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED": "false",
        "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": str(schedule_enabled).lower(),
        "PROPERTY_CATALOG_DEV_ORGANIZATION_ID": config.organization_id,
        "PROPERTY_CATALOG_DEV_WORKSPACE_ID": config.workspace_id,
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": config.workspace_id,
        "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST": ",".join(config.project_ids),
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
        "PROPERTY_CATALOG_DEV_ENVIRONMENT": "development",
        "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "DEV",
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
        "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID": (config.hot_producer_stream_id),
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": REVISION_FENCE_FILE,
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE": DRAIN_PROOF_FILE,
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE": (PRODUCER_RETIREMENT_FILE),
        "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY": RUNTIME_DIRECTORY,
        "PROPERTY_CATALOG_DEV_SPAN_SINCE": config.span_since,
        "PROPERTY_CATALOG_DEV_SPAN_UNTIL": config.span_until,
        "PROPERTY_CATALOG_DEV_SIDECAR_ACK": SIDECAR_ACK,
        "PROPERTY_CATALOG_DEV_MAX_WALL_MS": "100000",
        "TEMPORAL_HOST": config.temporal_host,
        "TEMPORAL_NAMESPACE": config.temporal_namespace,
        "TEMPORAL_TASK_QUEUE": TASK_QUEUE,
        "TEMPORAL_ALL_QUEUES": "false",
        "TEMPORAL_MAX_CONCURRENT_ACTIVITIES": "1",
        "TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS": "1",
        "TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT": str(TERMINATION_GRACE_SECONDS),
        "TEMPORAL_RELOAD_DISPATCHER_ON_START": "false",
        "TEMPORAL_RESOURCE_TUNING_ENABLED": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": f"{RUNTIME_DIRECTORY}/home",
        "TMPDIR": "/tmp",
        "XDG_CACHE_HOME": f"{RUNTIME_DIRECTORY}/cache",
    }
    if bootstrap_activation_sha256 is not None:
        data["PROPERTY_CATALOG_DEV_BOOTSTRAP_ACTIVATION_SHA256"] = (
            bootstrap_activation_sha256
        )
    return data


def render_documents(
    config: WorkloadConfig,
    *,
    bootstrap_activation_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Build and self-audit all resources before returning any document."""

    if not isinstance(config, WorkloadConfig):
        raise TypeError("config must be WorkloadConfig")
    if (
        bootstrap_activation_sha256 is not None
        and _SHA256_RE.fullmatch(bootstrap_activation_sha256) is None
    ):
        raise WorkloadValidationError(
            "schedule enablement requires a lowercase bootstrap activation SHA-256"
        )
    if (
        bootstrap_activation_sha256 is not None
        and len(set(bootstrap_activation_sha256)) < 4
    ):
        raise WorkloadValidationError(
            "schedule enablement refuses a placeholder bootstrap activation digest"
        )
    schedule_state = "enabled" if bootstrap_activation_sha256 else "disabled"
    bootstrap_digest = bootstrap_activation_sha256 or "disabled"
    common_annotations = {
        "futureagi.com/property-catalog-schedule": schedule_state,
        "futureagi.com/bootstrap-activation-sha256": bootstrap_digest,
    }
    labels = {
        "app.kubernetes.io/name": WORKLOAD_NAME,
        "app.kubernetes.io/part-of": "unified-property-catalog",
    }
    runtime_mount = {"name": "runtime", "mountPath": RUNTIME_DIRECTORY}

    collector_env = [
        _value_env("FI_CH_URL", config.source_clickhouse_http_url),
        _value_env("FI_CH_DATABASE", config.source_database),
        _secret_env("FI_CH_USERNAME", config.source_read_clickhouse_secret, "username"),
        _secret_env("FI_CH_PASSWORD", config.source_read_clickhouse_secret, "password"),
        _value_env("FI_GRPC_ADDR", "127.0.0.1:4317"),
        _value_env("FI_HTTP_ADDR", "127.0.0.1:4318"),
        _value_env(
            "FI_DEAD_LETTER_FILE",
            f"{RUNTIME_DIRECTORY}/span-dead-letter/dead_letter.jsonl",
        ),
        _value_env("FI_CATALOG_MODE", "disabled"),
        _value_env("FI_PROPERTY_CATALOG_MODE", "kafka"),
        _value_env("FI_PROPERTY_CATALOG_ENVIRONMENT", "development"),
        _value_env("FI_PROPERTY_CATALOG_DEV_ACK", GO_DEV_ACK),
        _value_env("FI_PROPERTY_CATALOG_EPOCH", config.epoch),
        _value_env("FI_PROPERTY_CATALOG_PROJECTION_VERSION", config.projection_version),
        _value_env(
            "FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID",
            config.hot_producer_stream_id,
        ),
        _value_env("FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST", config.workspace_id),
        _value_env("FI_PROPERTY_CATALOG_REVISION_FENCE_FILE", REVISION_FENCE_FILE),
        _value_env("FI_PROPERTY_CATALOG_SPOOL_DIR", RUNTIME_DIRECTORY),
        _value_env("FI_PROPERTY_CATALOG_REPLAY_INTERVAL", "1s"),
        _value_env("FI_PROPERTY_CATALOG_KAFKA_BROKERS", ",".join(config.kafka_brokers)),
        _value_env("FI_PROPERTY_CATALOG_KAFKA_TOPIC", config.kafka_topic),
    ]
    control_env = [
        _secret_env("CH25_USER", config.source_read_clickhouse_secret, "username"),
        _secret_env("CH25_PASSWORD", config.source_read_clickhouse_secret, "password"),
        _secret_env(
            "PROPERTY_CATALOG_DEV_WRITE_CH_USER",
            config.control_write_clickhouse_secret,
            "username",
        ),
        _secret_env(
            "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD",
            config.control_write_clickhouse_secret,
            "password",
        ),
    ]
    consumer_env = [
        _value_env("FI_PROPERTY_CATALOG_CONSUMER_MODE", "kafka"),
        _value_env("FI_PROPERTY_CATALOG_ENVIRONMENT", "development"),
        _value_env("FI_PROPERTY_CATALOG_DEV_ACK", GO_DEV_ACK),
        _value_env("FI_PROPERTY_CATALOG_CH_URL", config.target_clickhouse_http_url),
        _value_env("FI_PROPERTY_CATALOG_CH_DATABASE", config.target_database),
        _secret_env(
            "FI_PROPERTY_CATALOG_CH_USERNAME",
            config.consumer_write_clickhouse_secret,
            "username",
        ),
        _secret_env(
            "FI_PROPERTY_CATALOG_CH_PASSWORD",
            config.consumer_write_clickhouse_secret,
            "password",
        ),
        _value_env(
            "FI_PROPERTY_CATALOG_LEDGER_CH_URL", config.target_clickhouse_http_url
        ),
        _value_env("FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE", config.target_database),
        _secret_env(
            "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME",
            config.consumer_ledger_clickhouse_secret,
            "username",
        ),
        _secret_env(
            "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD",
            config.consumer_ledger_clickhouse_secret,
            "password",
        ),
        _value_env("FI_PROPERTY_CATALOG_KAFKA_BROKERS", ",".join(config.kafka_brokers)),
        _value_env("FI_PROPERTY_CATALOG_KAFKA_TOPIC", config.kafka_topic),
        _value_env(
            "FI_PROPERTY_CATALOG_KAFKA_CONSUMER_GROUP",
            config.kafka_consumer_group,
        ),
    ]

    documents: list[dict[str, Any]] = [
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": _metadata(
                SERVICE_ACCOUNT_NAME, config, component="control-plane"
            ),
            "automountServiceAccountToken": False,
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": _metadata(
                CONFIG_MAP_NAME,
                config,
                component="control-plane",
                annotations=common_annotations,
            ),
            "data": _config_map_data(config, bootstrap_activation_sha256),
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": _metadata(PVC_NAME, config, component="runtime-state"),
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "volumeMode": "Filesystem",
                "storageClassName": config.storage_class,
                "resources": {"requests": {"storage": config.storage_size}},
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": _metadata(
                WORKLOAD_NAME,
                config,
                component="producer-control-sidecar",
                annotations=common_annotations,
            ),
            "spec": {
                "replicas": 1,
                "revisionHistoryLimit": 2,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {**labels, "workload": WORKLOAD_NAME}},
                "template": {
                    "metadata": {
                        "labels": {**labels, "workload": WORKLOAD_NAME},
                        "annotations": common_annotations,
                    },
                    "spec": {
                        "serviceAccountName": SERVICE_ACCOUNT_NAME,
                        "automountServiceAccountToken": False,
                        "terminationGracePeriodSeconds": TERMINATION_GRACE_SECONDS,
                        "imagePullSecrets": [{"name": config.image_pull_secret}],
                        "securityContext": {
                            "fsGroup": RUNTIME_UID,
                            "fsGroupChangePolicy": "OnRootMismatch",
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "initContainers": [
                            {
                                "name": "runtime-volume-init",
                                "image": config.backend_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python", "-c"],
                                "args": [_RUNTIME_INIT_SCRIPT],
                                "securityContext": {
                                    "runAsUser": 0,
                                    "runAsGroup": 0,
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {
                                        "drop": ["ALL"],
                                        "add": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
                                    },
                                },
                                "volumeMounts": [runtime_mount],
                            }
                        ],
                        "containers": [
                            {
                                "name": "live-otlp-collector",
                                "image": config.collector_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["/usr/local/bin/fi-collector"],
                                "args": [
                                    "-config",
                                    "/etc/fi-collector/config.yaml",
                                ],
                                "envFrom": [
                                    {"secretRef": {"name": config.collector_env_secret}}
                                ],
                                "env": collector_env,
                                "ports": [
                                    {"name": "otlp-grpc", "containerPort": 4317},
                                    {"name": "otlp-http", "containerPort": 4318},
                                    {"name": "admin", "containerPort": 9464},
                                ],
                                "startupProbe": {
                                    "httpGet": {"path": "/healthz", "port": "admin"},
                                    "failureThreshold": 30,
                                    "periodSeconds": 2,
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": "/healthz", "port": "admin"},
                                    "periodSeconds": 5,
                                    "failureThreshold": 3,
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/healthz", "port": "admin"},
                                    "periodSeconds": 10,
                                    "failureThreshold": 3,
                                },
                                "securityContext": {
                                    "runAsNonRoot": True,
                                    "runAsUser": RUNTIME_UID,
                                    "runAsGroup": RUNTIME_UID,
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "512Mi"},
                                    "limits": {"cpu": "2", "memory": "2Gi"},
                                },
                                "volumeMounts": [runtime_mount],
                            },
                            {
                                "name": "control-plane",
                                "image": config.backend_image,
                                "imagePullPolicy": "IfNotPresent",
                                "workingDir": "/app/backend",
                                "command": [
                                    "python",
                                    "manage.py",
                                    "start_temporal_worker",
                                    "--task-queue",
                                    TASK_QUEUE,
                                    "--max-concurrent-activities",
                                    "1",
                                    "--max-concurrent-workflow-tasks",
                                    "1",
                                    "--graceful-timeout",
                                    str(TERMINATION_GRACE_SECONDS),
                                ],
                                "envFrom": [
                                    {"secretRef": {"name": config.backend_env_secret}},
                                    {"configMapRef": {"name": CONFIG_MAP_NAME}},
                                ],
                                "env": control_env,
                                "securityContext": {
                                    "runAsNonRoot": True,
                                    "runAsUser": RUNTIME_UID,
                                    "runAsGroup": RUNTIME_UID,
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "1Gi"},
                                    "limits": {"cpu": "2", "memory": "4Gi"},
                                },
                                "volumeMounts": [
                                    runtime_mount,
                                    {"name": "backend-tmp", "mountPath": "/tmp"},
                                    {
                                        "name": "backend-logs",
                                        "mountPath": "/app/backend/logs",
                                    },
                                    {
                                        "name": "backend-tfc-logs",
                                        "mountPath": "/app/backend/tfc/logs",
                                    },
                                ],
                            },
                        ],
                        "volumes": [
                            {
                                "name": "runtime",
                                "persistentVolumeClaim": {"claimName": PVC_NAME},
                            },
                            {
                                "name": "backend-tmp",
                                "emptyDir": {"sizeLimit": "256Mi"},
                            },
                            {
                                "name": "backend-logs",
                                "emptyDir": {"sizeLimit": "64Mi"},
                            },
                            {
                                "name": "backend-tfc-logs",
                                "emptyDir": {"sizeLimit": "64Mi"},
                            },
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": _metadata(
                SERVICE_NAME,
                config,
                component="otlp-canary",
                annotations={
                    "futureagi.com/routing": "manual-canary-only",
                    "futureagi.com/default-traffic": "disabled",
                    "futureagi.com/current-phase": "no-otlp-traffic",
                },
            ),
            "spec": {
                "type": "ClusterIP",
                # Deliberately unmatched. This phase uses historical SELECT-only
                # sources; any later hot-path traffic needs separate approval.
                "selector": {
                    **labels,
                    "workload": WORKLOAD_NAME,
                    "futureagi.com/otlp-admission": "separate-approval-required",
                },
                "ports": [
                    {"name": "otlp-grpc", "port": 4317, "targetPort": "otlp-grpc"},
                    {"name": "otlp-http", "port": 4318, "targetPort": "otlp-http"},
                    {"name": "admin", "port": 9464, "targetPort": "admin"},
                ],
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": _metadata(
                CONSUMER_NAME,
                config,
                component="consumer",
                annotations={
                    **common_annotations,
                    "futureagi.com/readiness-contract": (
                        "process-plus-kafka-group-lag-plus-clickhouse-ledger"
                    ),
                },
            ),
            "spec": {
                "replicas": 1,
                "revisionHistoryLimit": 2,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {**labels, "workload": CONSUMER_NAME}},
                "template": {
                    "metadata": {
                        "labels": {**labels, "workload": CONSUMER_NAME},
                        "annotations": common_annotations,
                    },
                    "spec": {
                        "serviceAccountName": SERVICE_ACCOUNT_NAME,
                        "automountServiceAccountToken": False,
                        "terminationGracePeriodSeconds": TERMINATION_GRACE_SECONDS,
                        "imagePullSecrets": [{"name": config.image_pull_secret}],
                        "securityContext": {
                            "seccompProfile": {"type": "RuntimeDefault"}
                        },
                        "containers": [
                            {
                                "name": "consumer",
                                "image": config.consumer_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": [
                                    "/usr/local/bin/fi-property-catalog-consumer"
                                ],
                                "args": ["--seed-from-delivery-ledger"],
                                "env": consumer_env,
                                "securityContext": {
                                    "runAsNonRoot": True,
                                    "runAsUser": RUNTIME_UID,
                                    "runAsGroup": RUNTIME_UID,
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "512Mi"},
                                    "limits": {"cpu": "2", "memory": "2Gi"},
                                },
                            }
                        ],
                    },
                },
            },
        },
    ]
    _validate_rendered_documents(
        documents,
        config=config,
        bootstrap_activation_sha256=bootstrap_activation_sha256,
    )
    return copy.deepcopy(documents)


def _resource(
    documents: Sequence[Mapping[str, Any]], kind: str, name: str
) -> Mapping[str, Any]:
    found = [
        value
        for value in documents
        if value.get("kind") == kind
        and isinstance(value.get("metadata"), Mapping)
        and value["metadata"].get("name") == name
    ]
    if len(found) != 1:
        raise WorkloadValidationError(
            f"rendered manifest lacks one exact {kind}/{name}"
        )
    return found[0]


def _env_values(container: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in container.get("env", []):
        if "value" in value:
            result[str(value["name"])] = str(value["value"])
    return result


def _secret_env_refs(container: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for value in container.get("env", []):
        if "valueFrom" not in value:
            continue
        if set(value) != {"name", "valueFrom"}:
            raise WorkloadValidationError(
                "credential environment entry is not canonical"
            )
        reference = value["valueFrom"]
        if not isinstance(reference, Mapping) or set(reference) != {"secretKeyRef"}:
            raise WorkloadValidationError(
                "credential must use one Secret key reference"
            )
        secret_key = reference["secretKeyRef"]
        if not isinstance(secret_key, Mapping) or set(secret_key) != {"name", "key"}:
            raise WorkloadValidationError("Secret key reference is not canonical")
        result[str(value["name"])] = (
            str(secret_key["name"]),
            str(secret_key["key"]),
        )
    return result


def _hardened_nonroot(container: Mapping[str, Any]) -> bool:
    return container.get("securityContext") == {
        "runAsNonRoot": True,
        "runAsUser": RUNTIME_UID,
        "runAsGroup": RUNTIME_UID,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }


def _hardened_volume_init(container: Mapping[str, Any]) -> bool:
    return container.get("securityContext") == {
        "runAsUser": 0,
        "runAsGroup": 0,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {
            "drop": ["ALL"],
            "add": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
        },
    }


def _validate_rendered_documents(
    documents: Sequence[Mapping[str, Any]],
    *,
    config: WorkloadConfig,
    bootstrap_activation_sha256: str | None,
) -> None:
    if len(documents) != 6 or any(value.get("kind") == "Secret" for value in documents):
        raise WorkloadValidationError(
            "renderer must emit exactly six resources and no Secret objects"
        )
    if any(
        value.get("metadata", {}).get("namespace") != config.namespace
        for value in documents
    ):
        raise WorkloadValidationError(
            "rendered resource crossed the configured namespace"
        )

    config_map = _resource(documents, "ConfigMap", CONFIG_MAP_NAME)
    config_data = config_map.get("data", {})
    expected_schedule = "true" if bootstrap_activation_sha256 else "false"
    if (
        config_data.get("PROPERTY_CATALOG_DEV_RECONCILE_ENABLED") != expected_schedule
        or config_data.get("PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED") != "false"
        or config_data.get("SPAN_ATTRIBUTE_CATALOG_READ_MODE") != "off"
        or config_data.get("SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED") != "false"
        or config_data.get("PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST")
        != config.workspace_id
        or config_data.get("PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME")
        != config.write_clickhouse_hostname
        or config_data.get("PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME")
        != config.source_clickhouse_hostname
        or config_data.get("PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE")
        != config.postgres_database
        or config_data.get("PROPERTY_CATALOG_DEV_EXPECTED_PG_USER")
        != config.postgres_user
        or config_data.get("PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS")
        != config.postgres_server_address
        or config_data.get("PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT")
        != str(config.postgres_server_port)
        or config_data.get("PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY")
        != RUNTIME_DIRECTORY
        or config_data.get("PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE")
        != REVISION_FENCE_FILE
        or config_data.get("PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE") != DRAIN_PROOF_FILE
        or config_data.get("PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE")
        != PRODUCER_RETIREMENT_FILE
    ):
        raise WorkloadValidationError("rendered control-plane contract drifted")

    pvc = _resource(documents, "PersistentVolumeClaim", PVC_NAME)
    if (
        pvc.get("spec", {}).get("accessModes") != ["ReadWriteOnce"]
        or pvc.get("spec", {}).get("volumeMode") != "Filesystem"
    ):
        raise WorkloadValidationError("runtime PVC is not one RWO filesystem")

    service_account = _resource(documents, "ServiceAccount", SERVICE_ACCOUNT_NAME)
    if service_account.get("automountServiceAccountToken") is not False:
        raise WorkloadValidationError(
            "service-account token automount must be disabled"
        )

    workload = _resource(documents, "Deployment", WORKLOAD_NAME)
    consumer = _resource(documents, "Deployment", CONSUMER_NAME)
    for deployment in (workload, consumer):
        spec = deployment.get("spec", {})
        pod_spec = spec.get("template", {}).get("spec", {})
        if (
            spec.get("replicas") != 1
            or spec.get("strategy") != {"type": "Recreate"}
            or pod_spec.get("terminationGracePeriodSeconds")
            != TERMINATION_GRACE_SECONDS
        ):
            raise WorkloadValidationError(
                "every deployment must be replicas=1, Recreate, and drain for 180s"
            )
        if (
            pod_spec.get("serviceAccountName") != SERVICE_ACCOUNT_NAME
            or pod_spec.get("automountServiceAccountToken") is not False
            or pod_spec.get("securityContext", {}).get("seccompProfile")
            != {"type": "RuntimeDefault"}
        ):
            raise WorkloadValidationError(
                "pod identity, token, or seccomp boundary drifted"
            )

    workload_pod = workload["spec"]["template"]["spec"]
    expected_volumes = [
        {
            "name": "runtime",
            "persistentVolumeClaim": {"claimName": PVC_NAME},
        },
        {"name": "backend-tmp", "emptyDir": {"sizeLimit": "256Mi"}},
        {"name": "backend-logs", "emptyDir": {"sizeLimit": "64Mi"}},
        {"name": "backend-tfc-logs", "emptyDir": {"sizeLimit": "64Mi"}},
    ]
    if workload_pod.get("volumes") != expected_volumes or any(
        "hostPath" in volume for volume in workload_pod.get("volumes", [])
    ):
        raise WorkloadValidationError(
            "pod must have one reviewed PVC and only bounded backend emptyDirs"
        )
    init_containers = workload_pod.get("initContainers", [])
    if len(init_containers) != 1:
        raise WorkloadValidationError("runtime volume requires one exact initializer")
    runtime_init = init_containers[0]
    if (
        runtime_init.get("image") != config.backend_image
        or runtime_init.get("command") != ["python", "-c"]
        or runtime_init.get("args") != [_RUNTIME_INIT_SCRIPT]
        or runtime_init.get("volumeMounts")
        != [{"name": "runtime", "mountPath": RUNTIME_DIRECTORY}]
        or not _hardened_volume_init(runtime_init)
    ):
        raise WorkloadValidationError("runtime volume initializer drifted")

    containers = workload["spec"]["template"]["spec"]["containers"]
    if [value.get("name") for value in containers] != [
        "live-otlp-collector",
        "control-plane",
    ]:
        raise WorkloadValidationError("producer/control containers are not one pod")
    collector, control = containers
    if collector.get("image") != config.collector_image:
        raise WorkloadValidationError("live collector image drifted")
    if control.get("image") != config.backend_image:
        raise WorkloadValidationError("control image drifted")
    expected_mount = [{"name": "runtime", "mountPath": RUNTIME_DIRECTORY}]
    expected_control_mounts = [
        *expected_mount,
        {"name": "backend-tmp", "mountPath": "/tmp"},
        {"name": "backend-logs", "mountPath": "/app/backend/logs"},
        {"name": "backend-tfc-logs", "mountPath": "/app/backend/tfc/logs"},
    ]
    if (
        collector.get("volumeMounts") != expected_mount
        or control.get("volumeMounts") != expected_control_mounts
    ):
        raise WorkloadValidationError(
            "Python and Go do not share the exact runtime mount"
        )
    if not _hardened_nonroot(collector) or not _hardened_nonroot(control):
        raise WorkloadValidationError("producer/control container hardening drifted")
    if control.get("command") != [
        "python",
        "manage.py",
        "start_temporal_worker",
        "--task-queue",
        TASK_QUEUE,
        "--max-concurrent-activities",
        "1",
        "--max-concurrent-workflow-tasks",
        "1",
        "--graceful-timeout",
        str(TERMINATION_GRACE_SECONDS),
    ]:
        raise WorkloadValidationError("dedicated Temporal worker command drifted")
    if collector.get("envFrom") != [
        {"secretRef": {"name": config.collector_env_secret}}
    ] or control.get("envFrom") != [
        {"secretRef": {"name": config.backend_env_secret}},
        {"configMapRef": {"name": CONFIG_MAP_NAME}},
    ]:
        raise WorkloadValidationError("purpose-built workload Secret refs drifted")
    if _secret_env_refs(collector) != {
        "FI_CH_USERNAME": (config.source_read_clickhouse_secret, "username"),
        "FI_CH_PASSWORD": (config.source_read_clickhouse_secret, "password"),
    }:
        raise WorkloadValidationError(
            "collector must use the server-enforced source read-only identity"
        )
    if _secret_env_refs(control) != {
        "CH25_USER": (config.source_read_clickhouse_secret, "username"),
        "CH25_PASSWORD": (config.source_read_clickhouse_secret, "password"),
        "PROPERTY_CATALOG_DEV_WRITE_CH_USER": (
            config.control_write_clickhouse_secret,
            "username",
        ),
        "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD": (
            config.control_write_clickhouse_secret,
            "password",
        ),
    }:
        raise WorkloadValidationError("control-plane ClickHouse Secret refs drifted")
    collector_values = _env_values(collector)
    if (
        collector_values.get("FI_CATALOG_MODE") != "disabled"
        or collector_values.get("FI_PROPERTY_CATALOG_MODE") != "kafka"
        or collector_values.get("FI_GRPC_ADDR") != "127.0.0.1:4317"
        or collector_values.get("FI_HTTP_ADDR") != "127.0.0.1:4318"
        or collector_values.get("FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST")
        != config.workspace_id
        or collector_values.get("FI_PROPERTY_CATALOG_REVISION_FENCE_FILE")
        != REVISION_FENCE_FILE
        or collector_values.get("FI_PROPERTY_CATALOG_SPOOL_DIR") != RUNTIME_DIRECTORY
    ):
        raise WorkloadValidationError("rendered collector safety gates drifted")

    consumer_container = consumer["spec"]["template"]["spec"]["containers"]
    if len(consumer_container) != 1:
        raise WorkloadValidationError("consumer deployment must contain one process")
    consumer_process = consumer_container[0]
    if (
        consumer_process.get("image") != config.collector_image
        or consumer_process.get("command")
        != ["/usr/local/bin/fi-property-catalog-consumer"]
        or consumer_process.get("args") != ["--seed-from-delivery-ledger"]
    ):
        raise WorkloadValidationError("consumer image or durable command drifted")
    if not _hardened_nonroot(consumer_process):
        raise WorkloadValidationError("consumer container hardening drifted")
    if _secret_env_refs(consumer_process) != {
        "FI_PROPERTY_CATALOG_CH_USERNAME": (
            config.consumer_write_clickhouse_secret,
            "username",
        ),
        "FI_PROPERTY_CATALOG_CH_PASSWORD": (
            config.consumer_write_clickhouse_secret,
            "password",
        ),
        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": (
            config.consumer_ledger_clickhouse_secret,
            "username",
        ),
        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": (
            config.consumer_ledger_clickhouse_secret,
            "password",
        ),
    }:
        raise WorkloadValidationError("consumer ClickHouse Secret refs drifted")
    consumer_values = _env_values(consumer_process)
    if (
        consumer_values.get("FI_PROPERTY_CATALOG_CH_URL")
        != consumer_values.get("FI_PROPERTY_CATALOG_LEDGER_CH_URL")
        or consumer_values.get("FI_PROPERTY_CATALOG_CH_DATABASE")
        != consumer_values.get("FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE")
        or consumer_values.get("FI_PROPERTY_CATALOG_KAFKA_TOPIC") != config.kafka_topic
    ):
        raise WorkloadValidationError("consumer writer/ledger/topic contract drifted")

    service = _resource(documents, "Service", SERVICE_NAME)
    workload_labels = (
        workload.get("spec", {})
        .get("template", {})
        .get("metadata", {})
        .get("labels", {})
    )
    if (
        [value.get("port") for value in service.get("spec", {}).get("ports", [])]
        != [4317, 4318, 9464]
        or service.get("metadata", {}).get("annotations")
        != {
            "futureagi.com/routing": "manual-canary-only",
            "futureagi.com/default-traffic": "disabled",
            "futureagi.com/current-phase": "no-otlp-traffic",
        }
        or service.get("spec", {})
        .get("selector", {})
        .get("futureagi.com/otlp-admission")
        != "separate-approval-required"
        or "futureagi.com/otlp-admission" in workload_labels
    ):
        raise WorkloadValidationError("manual-canary OTLP Service contract drifted")


def render_yaml(
    config: WorkloadConfig,
    *,
    bootstrap_activation_sha256: str | None = None,
) -> str:
    documents = render_documents(
        config,
        bootstrap_activation_sha256=bootstrap_activation_sha256,
    )
    rendered = yaml.safe_dump_all(
        documents,
        explicit_start=True,
        sort_keys=False,
        width=100,
    )
    if not rendered.endswith("\n"):
        rendered += "\n"
    # Parse the final bytes once more so serialization cannot bypass the object
    # audit above. SafeLoader also proves the output is applyable YAML syntax.
    round_trip = list(yaml.safe_load_all(rendered))
    if round_trip != documents:
        raise WorkloadValidationError("rendered YAML did not round-trip exactly")
    return rendered


def write_rendered(path: Path, rendered: str) -> None:
    """Atomically publish a validated manifest without exposing partial YAML."""

    if not isinstance(rendered, str) or not rendered.startswith("---\n"):
        raise WorkloadValidationError("refusing to write unvalidated manifest bytes")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    keep = True
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        keep = False
    finally:
        if keep:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and render the one-workspace DEV property-catalog workload"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--bootstrap-activation-sha256",
        help=(
            "explicitly enable the 120s schedule only after a completed bootstrap; "
            "omit to render the default disabled state"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate and self-audit without writing or printing YAML",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.check and args.output is not None:
        sys.stderr.write("--check and --output are mutually exclusive\n")
        return 2
    try:
        config = load_config(args.config)
        rendered = render_yaml(
            config,
            bootstrap_activation_sha256=args.bootstrap_activation_sha256,
        )
        if args.check:
            return 0
        if args.output is not None:
            write_rendered(args.output, rendered)
        else:
            sys.stdout.write(rendered)
    except (OSError, WorkloadValidationError, yaml.YAMLError) as exc:
        sys.stderr.write(f"property-catalog DEV workload rejected: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
