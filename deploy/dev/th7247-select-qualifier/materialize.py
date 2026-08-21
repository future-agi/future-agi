#!/usr/bin/env python3
"""Materialize one source-bound TH-7247 qualifier Job for an exact DEV target.

This program is deliberately offline.  It reads a reviewed bundle, private
operator configuration, kubeconfig, and gcloud configuration files; it never
invokes kubectl, gcloud, a container runtime, or a network client.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

FORMAT = "futureagi.th7247-select-qualifier-dev"
VERSION = 1
QUALIFIER_SCHEMA = "th7247-current-select-only/v2"
QUALIFIER_SHARDS = (
    "whatfix",
    "colektia",
    "mudflap",
    "trace_system",
    "whatfix_graphs",
    "colektia_graphs",
)
WORKLOAD_NAME = "th7247-current-select-qualifier-dev"
WORKLOAD_LABEL = "th7247-current-select-qualifier"
CATALOG_ACK = "I_ACKNOWLEDGE_DEV_ONLY_UNIFIED_PROPERTY_CATALOG"
MAX_FROZEN_END_AGE = timedelta(hours=10)
_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DEV_TOKEN_RE = re.compile(r"(?:^|[-._/:])(dev|development)(?:$|[-._/:])")
_PRODUCTION_TOKEN_RE = re.compile(
    r"(?:^|[-._/:])(prod|production|live)(?:$|[-._/:])"
)
_TOP_LEVEL_FIELDS = (
    "format",
    "version",
    "gcp",
    "kubernetes",
    "image",
    "catalog",
    "network",
)
_GCP_FIELDS = ("project", "kube_context")
_KUBERNETES_FIELDS = (
    "namespace",
    "service_account",
    "read_only_runtime_secret",
    "read_only_secret_contract_sha256",
    "image_pull_secret",
)
_IMAGE_FIELDS = ("derived",)
_CATALOG_FIELDS = ("database", "workspace_allowlist")
_NETWORK_FIELDS = ("dns_namespace", "dns_pod_labels", "egress")
_EGRESS_FIELDS = ("name", "purpose", "cidr", "ports")
_EGRESS_PURPOSES = frozenset(
    {"postgresql", "clickhouse_source", "clickhouse_catalog"}
)
_EGRESS_PORTS = {
    "postgresql": frozenset({5432, 6432}),
    "clickhouse_source": frozenset({8123, 8443, 9000, 9440}),
    "clickhouse_catalog": frozenset({8123, 8443, 9000, 9440}),
}


class MaterializationError(RuntimeError):
    """An offline launch boundary was incomplete or unsafe."""


@dataclass(frozen=True)
class EgressTarget:
    name: str
    purpose: str
    cidr: str
    ports: tuple[int, ...]


@dataclass(frozen=True)
class Config:
    gcp_project: str
    kube_context: str
    namespace: str
    service_account: str
    runtime_secret: str
    runtime_secret_contract_sha256: str
    image_pull_secret: str
    derived_image: str
    catalog_database: str
    workspace_allowlist: tuple[str, ...]
    dns_namespace: str
    dns_pod_labels: dict[str, str]
    egress: tuple[EgressTarget, ...]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MaterializationError(f"{label} must be a string-keyed mapping")
    return value


def _exact_fields(value: dict[str, Any], expected: tuple[str, ...], label: str) -> None:
    if tuple(value) != expected:
        raise MaterializationError(f"{label} fields or canonical order drifted")


def _text(value: Any, label: str, *, dev: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MaterializationError(f"{label} must be a non-empty trimmed string")
    lowered = value.lower()
    if _PRODUCTION_TOKEN_RE.search(lowered):
        raise MaterializationError(f"{label} contains a production/live token")
    if dev and not _DEV_TOKEN_RE.search(lowered):
        raise MaterializationError(f"{label} does not contain an exact DEV token")
    return value


def _dns_label(value: Any, label: str, *, dev: bool = False) -> str:
    text = _text(value, label, dev=dev)
    if not _DNS_LABEL_RE.fullmatch(text):
        raise MaterializationError(f"{label} is not a DNS label")
    return text


def _purpose_built_name(value: Any, label: str, required: tuple[str, ...]) -> str:
    name = _dns_label(value, label, dev=True)
    tokens = set(re.split(r"[-._]+", name.lower()))
    if not set(required).issubset(tokens):
        raise MaterializationError(f"{label} is not purpose-built for this qualifier")
    if any(token in tokens for token in ("core", "general", "backend")):
        raise MaterializationError(f"{label} looks like a reusable application secret")
    return name


def _uuid4(value: Any, label: str) -> str:
    import uuid

    text = _text(value, label)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise MaterializationError(f"{label} is not a UUID") from exc
    if parsed.version != 4 or str(parsed) != text:
        raise MaterializationError(f"{label} must be canonical UUIDv4")
    return text


def load_config(path: Path) -> Config:
    raw = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "config")
    _exact_fields(raw, _TOP_LEVEL_FIELDS, "config")
    if raw["format"] != FORMAT or raw["version"] != VERSION:
        raise MaterializationError("config format/version is not current")

    gcp = _mapping(raw["gcp"], "gcp")
    kubernetes = _mapping(raw["kubernetes"], "kubernetes")
    image = _mapping(raw["image"], "image")
    catalog = _mapping(raw["catalog"], "catalog")
    network = _mapping(raw["network"], "network")
    _exact_fields(gcp, _GCP_FIELDS, "gcp")
    _exact_fields(kubernetes, _KUBERNETES_FIELDS, "kubernetes")
    _exact_fields(image, _IMAGE_FIELDS, "image")
    _exact_fields(catalog, _CATALOG_FIELDS, "catalog")
    _exact_fields(network, _NETWORK_FIELDS, "network")

    gcp_project = _text(gcp["project"], "GCP project", dev=True)
    kube_context = _text(gcp["kube_context"], "kube context", dev=True)
    if not kube_context.startswith(f"gke_{gcp_project}_"):
        raise MaterializationError("kube context is not bound to the exact GCP project")
    namespace = _dns_label(kubernetes["namespace"], "namespace", dev=True)
    service_account = _purpose_built_name(
        kubernetes["service_account"],
        "service account",
        ("th7247", "qualifier", "dev"),
    )
    runtime_secret = _purpose_built_name(
        kubernetes["read_only_runtime_secret"],
        "runtime Secret",
        ("th7247", "qualifier", "dev", "readonly"),
    )
    contract_sha256 = _text(
        kubernetes["read_only_secret_contract_sha256"],
        "runtime Secret contract SHA-256",
    )
    if not _SHA256_RE.fullmatch(contract_sha256):
        raise MaterializationError("runtime Secret contract SHA-256 is invalid")
    if contract_sha256 == "0" * 64:
        raise MaterializationError("runtime Secret contract uses the example digest")
    image_pull_secret = _purpose_built_name(
        kubernetes["image_pull_secret"],
        "image-pull Secret",
        ("th7247", "qualifier", "dev", "pull"),
    )
    if image_pull_secret == runtime_secret:
        raise MaterializationError("runtime and image-pull Secrets must be distinct")
    derived_image = _text(image["derived"], "derived image", dev=True)
    if not _IMAGE_RE.fullmatch(derived_image):
        raise MaterializationError("derived image must be digest pinned")
    if derived_image.endswith("0" * 64):
        raise MaterializationError("derived image uses the example digest")

    catalog_database = _text(catalog["database"], "catalog database", dev=True)
    if re.fullmatch(r"th7247_catalog_dev_[a-z0-9][a-z0-9_]*", catalog_database) is None:
        raise MaterializationError("catalog database is not an isolated TH-7247 DEV name")
    raw_workspaces = catalog["workspace_allowlist"]
    if not isinstance(raw_workspaces, list) or not 1 <= len(raw_workspaces) <= 32:
        raise MaterializationError("workspace allowlist must contain 1..32 UUIDs")
    workspace_allowlist = tuple(
        _uuid4(value, f"workspace_allowlist[{index}]")
        for index, value in enumerate(raw_workspaces)
    )
    if workspace_allowlist != tuple(sorted(set(workspace_allowlist))):
        raise MaterializationError("workspace allowlist must be sorted and unique")

    dns_namespace = _dns_label(network["dns_namespace"], "DNS namespace")
    if dns_namespace != "kube-system":
        raise MaterializationError("DNS egress must target kube-system")
    dns_labels = _mapping(network["dns_pod_labels"], "DNS pod labels")
    if dns_labels != {"k8s-app": "kube-dns"}:
        raise MaterializationError("DNS egress selector must be exactly kube-dns")
    raw_egress = network["egress"]
    if not isinstance(raw_egress, list) or not 3 <= len(raw_egress) <= 8:
        raise MaterializationError("network egress must contain 3..8 exact targets")
    egress: list[EgressTarget] = []
    for index, raw_target in enumerate(raw_egress):
        target = _mapping(raw_target, f"network.egress[{index}]")
        _exact_fields(target, _EGRESS_FIELDS, f"network.egress[{index}]")
        name = _dns_label(target["name"], f"network.egress[{index}].name")
        purpose = _text(target["purpose"], f"network.egress[{index}].purpose")
        if purpose not in _EGRESS_PURPOSES:
            raise MaterializationError("network egress purpose is not allowlisted")
        try:
            cidr = ipaddress.ip_network(str(target["cidr"]), strict=True)
        except ValueError as exc:
            raise MaterializationError("network egress CIDR is invalid") from exc
        if (
            cidr.version != 4
            or cidr.prefixlen != 32
            or cidr.network_address.is_loopback
            or cidr.network_address.is_link_local
            or cidr.network_address.is_multicast
            or cidr.network_address.is_unspecified
            or cidr.network_address.is_reserved
        ):
            raise MaterializationError("network egress must be one safe IPv4 /32")
        raw_ports = target["ports"]
        if not isinstance(raw_ports, list) or not 1 <= len(raw_ports) <= 4:
            raise MaterializationError("network egress ports must contain 1..4 values")
        ports = tuple(raw_ports)
        if (
            any(isinstance(port, bool) or not isinstance(port, int) for port in ports)
            or any(not 1 <= port <= 65_535 for port in ports)
            or ports != tuple(sorted(set(ports)))
        ):
            raise MaterializationError("network egress ports must be sorted unique TCP ports")
        if not set(ports).issubset(_EGRESS_PORTS[purpose]):
            raise MaterializationError(
                "network egress port is not valid for its database purpose"
            )
        egress.append(EgressTarget(name, purpose, str(cidr), ports))
    if len({target.name for target in egress}) != len(egress):
        raise MaterializationError("network egress names must be unique")
    if {target.purpose for target in egress} != _EGRESS_PURPOSES:
        raise MaterializationError("network egress must cover PG, source CH, and catalog CH")
    if tuple((row.purpose, row.cidr, row.ports) for row in egress) != tuple(
        sorted((row.purpose, row.cidr, row.ports) for row in egress)
    ):
        raise MaterializationError("network egress targets must be canonically sorted")

    return Config(
        gcp_project=gcp_project,
        kube_context=kube_context,
        namespace=namespace,
        service_account=service_account,
        runtime_secret=runtime_secret,
        runtime_secret_contract_sha256=contract_sha256,
        image_pull_secret=image_pull_secret,
        derived_image=derived_image,
        catalog_database=catalog_database,
        workspace_allowlist=workspace_allowlist,
        dns_namespace=dns_namespace,
        dns_pod_labels=dict(dns_labels),
        egress=tuple(egress),
    )


def _load_yaml_object(path: Path, label: str) -> dict[str, Any]:
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    if len(documents) != 1:
        raise MaterializationError(f"{label} must contain exactly one document")
    return _mapping(documents[0], label)


def verify_local_target(
    config: Config,
    *,
    kubeconfig_path: Path,
    gcloud_active_config_path: Path,
    gcloud_configurations_dir: Path,
) -> dict[str, str]:
    kubeconfig = _load_yaml_object(kubeconfig_path, "kubeconfig")
    current_context = kubeconfig.get("current-context")
    if current_context != config.kube_context:
        raise MaterializationError("current kube context does not equal reviewed DEV context")
    contexts = kubeconfig.get("contexts")
    if not isinstance(contexts, list):
        raise MaterializationError("kubeconfig contexts are invalid")
    matches = [
        row
        for row in contexts
        if isinstance(row, dict) and row.get("name") == current_context
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("context"), dict):
        raise MaterializationError("reviewed kube context is absent or ambiguous")
    context = matches[0]["context"]
    if context.get("namespace") != config.namespace:
        raise MaterializationError("kube context has no exact reviewed DEV namespace")
    if context.get("cluster") != config.kube_context:
        raise MaterializationError("kube context cluster identity drifted")
    clusters = kubeconfig.get("clusters")
    cluster_matches = [
        row
        for row in clusters or []
        if isinstance(row, dict) and row.get("name") == config.kube_context
    ]
    if len(cluster_matches) != 1 or not isinstance(
        cluster_matches[0].get("cluster"), dict
    ):
        raise MaterializationError("reviewed kube cluster is absent or ambiguous")
    server = str(cluster_matches[0]["cluster"].get("server") or "")
    parsed_server = urlsplit(server)
    if parsed_server.scheme != "https" or not parsed_server.hostname:
        raise MaterializationError("reviewed kube API server is not an HTTPS endpoint")
    try:
        api_ip = ipaddress.ip_address(parsed_server.hostname)
    except ValueError:
        api_ip = None
    if api_ip is not None and any(
        api_ip == ipaddress.ip_network(target.cidr).network_address
        for target in config.egress
    ):
        raise MaterializationError("database egress allowlist includes kube API server")

    active_name = gcloud_active_config_path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", active_name):
        raise MaterializationError("active gcloud configuration name is invalid")
    parser = configparser.ConfigParser(interpolation=None)
    config_path = gcloud_configurations_dir / f"config_{active_name}"
    if not config_path.is_file() or config_path.is_symlink():
        raise MaterializationError("active gcloud configuration file is absent")
    parser.read(config_path, encoding="utf-8")
    project = parser.get("core", "project", fallback="").strip()
    if project != config.gcp_project:
        raise MaterializationError("active gcloud project does not equal reviewed DEV project")
    return {
        "gcp_project": project,
        "kube_context": str(current_context),
        "namespace": config.namespace,
        "kube_api_host_sha256": hashlib.sha256(
            parsed_server.hostname.encode("utf-8")
        ).hexdigest(),
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(
        value
        and "\\" not in value
        and not path.is_absolute()
        and "." not in path.parts
        and ".." not in path.parts
        and path.as_posix() == value
    )


def load_bundle(bundle: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not bundle.is_dir() or bundle.is_symlink():
        raise MaterializationError("bundle path is not a physical directory")
    bundle_manifest_path = bundle / "bundle-manifest.json"
    if not bundle_manifest_path.is_file() or bundle_manifest_path.is_symlink():
        raise MaterializationError("bundle manifest is absent")
    manifest = _mapping(
        json.loads(bundle_manifest_path.read_text(encoding="utf-8")),
        "bundle manifest",
    )
    if manifest.get("schema") != f"{QUALIFIER_SCHEMA}/bundle":
        raise MaterializationError("bundle schema is not current")
    artifacts = _mapping(manifest.get("artifacts"), "bundle artifacts")
    expected_artifacts = {
        "Dockerfile",
        "harness.tar",
        "job.yaml.template",
        "runtime-overlay.tar",
        "source-manifest.json",
    }
    if set(artifacts) != expected_artifacts:
        raise MaterializationError("bundle artifact inventory is not exact")
    for name, expected_sha256 in artifacts.items():
        path = bundle / name
        if (
            not isinstance(expected_sha256, str)
            or not _SHA256_RE.fullmatch(expected_sha256)
            or not path.is_file()
            or path.is_symlink()
            or _sha256_file(path) != expected_sha256
        ):
            raise MaterializationError(f"bundle artifact failed verification: {name}")
    source_manifest_path = bundle / "source-manifest.json"
    if _sha256_file(source_manifest_path) != manifest.get("source_manifest_sha256"):
        raise MaterializationError("bundle source-manifest binding drifted")
    source_manifest = _mapping(
        json.loads(source_manifest_path.read_text(encoding="utf-8")),
        "source manifest",
    )
    if (
        source_manifest.get("schema") != QUALIFIER_SCHEMA
        or source_manifest.get("base_commit") != manifest.get("base_commit")
        or source_manifest.get("base_image") != manifest.get("base_image")
    ):
        raise MaterializationError("bundle source identity is inconsistent")
    runtime_deletions = source_manifest.get("runtime_deletions")
    if (
        not isinstance(runtime_deletions, list)
        or runtime_deletions != sorted(set(runtime_deletions))
        or not all(isinstance(value, str) and value for value in runtime_deletions)
    ):
        raise MaterializationError("bundle runtime deletion contract is invalid")
    runtime_files = source_manifest.get("runtime_files")
    deletion_hashes = source_manifest.get("runtime_deletion_base_sha256")
    if not isinstance(runtime_files, dict) or not isinstance(deletion_hashes, dict):
        raise MaterializationError("bundle runtime source maps are invalid")
    if set(deletion_hashes) != set(runtime_deletions):
        raise MaterializationError("bundle runtime deletion hash map is not exact")
    if set(runtime_files) & set(runtime_deletions):
        raise MaterializationError("bundle runtime files overlap runtime deletions")
    for label, values in (
        ("runtime_files", runtime_files),
        ("runtime_deletion_base_sha256", deletion_hashes),
    ):
        for relative, digest in values.items():
            if (
                not isinstance(relative, str)
                or not _safe_relative_path(relative)
                or not isinstance(digest, str)
                or not _SHA256_RE.fullmatch(digest)
            ):
                raise MaterializationError(f"bundle {label} entry is invalid")
    return manifest, source_manifest


def _parse_frozen_end(value: str, *, now: datetime | None = None) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise MaterializationError("frozen end must be UTC whole seconds")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    current = datetime.now(UTC) if now is None else now.astimezone(UTC)
    if parsed > current + timedelta(minutes=1) or current - parsed > MAX_FROZEN_END_AGE:
        raise MaterializationError("frozen end is outside the ten-hour launch window")
    return parsed


def _result_binding(payload: dict[str, Any]) -> tuple[Any, ...]:
    source = payload.get("source_identity")
    if not isinstance(source, dict):
        raise MaterializationError("prior result omitted source identity")
    return (
        payload.get("schema"),
        payload.get("run_id"),
        payload.get("frozen_end"),
        source.get("base_commit"),
        source.get("derived_image_digest"),
        source.get("source_manifest_sha256"),
        source.get("qualifier_sha256"),
    )


def validate_prior_results(
    paths: list[Path],
    *,
    shard: str,
    run_id: str,
    frozen_end: str,
    config: Config,
    bundle_manifest: dict[str, Any],
) -> str:
    shard_index = QUALIFIER_SHARDS.index(shard)
    if len(paths) != shard_index:
        raise MaterializationError("prior results do not exactly precede this shard")
    digests: list[str] = []
    expected_binding = (
        QUALIFIER_SCHEMA,
        run_id,
        datetime.strptime(frozen_end, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=UTC)
        .isoformat(),
        bundle_manifest.get("base_commit"),
        config.derived_image,
        bundle_manifest.get("source_manifest_sha256"),
        bundle_manifest.get("qualifier_sha256"),
    )
    for index, path in enumerate(paths):
        if not path.is_file() or path.is_symlink():
            raise MaterializationError("prior result is not a physical file")
        raw = path.read_bytes()
        try:
            payload = _mapping(json.loads(raw), "prior result")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MaterializationError("prior result is not strict JSON") from exc
        if (
            payload.get("qualified") is not True
            or payload.get("exit_code") != 0
            or payload.get("shard") != QUALIFIER_SHARDS[index]
            or payload.get("shard_index") != index
            or payload.get("shard_count") != len(QUALIFIER_SHARDS)
            or _result_binding(payload) != expected_binding
        ):
            raise MaterializationError("prior result is not a green source-bound predecessor")
        counts = payload.get("counts")
        if not isinstance(counts, dict) or any(
            counts.get(name) != 0
            for name in (
                "pg_blocked",
                "ch_blocked",
                "redis_blocked",
                "celery_blocked",
                "temporal_blocked",
                "scheduler_blocked",
                "external_cache_blocked",
            )
        ):
            raise MaterializationError("prior result activated a mutation tripwire")
        digests.append(hashlib.sha256(raw).hexdigest())
    return hashlib.sha256("\n".join(digests).encode("ascii")).hexdigest()


def _env_map(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    env = container.get("env")
    if not isinstance(env, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in env
    ):
        raise MaterializationError("Job environment is invalid")
    result = {row["name"]: row for row in env}
    if len(result) != len(env):
        raise MaterializationError("Job environment contains duplicate names")
    return result


def _set_env(container: dict[str, Any], name: str, value: str) -> None:
    env = _env_map(container)
    env[name] = {"name": name, "value": value}
    container["env"] = [env[key] for key in sorted(env)]


def _load_job_template(bundle: Path) -> dict[str, Any]:
    job = _load_yaml_object(bundle / "job.yaml.template", "Job template")
    if job.get("apiVersion") != "batch/v1" or job.get("kind") != "Job":
        raise MaterializationError("bundle Job template kind drifted")
    spec = _mapping(job.get("spec"), "Job spec")
    pod = _mapping(_mapping(spec.get("template"), "pod template").get("spec"), "pod spec")
    containers = pod.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise MaterializationError("qualifier Job must contain exactly one container")
    container = _mapping(containers[0], "qualifier container")
    if (
        spec.get("activeDeadlineSeconds") != 5_400
        or spec.get("backoffLimit") != 0
        or pod.get("restartPolicy") != "Never"
        or pod.get("automountServiceAccountToken") is not False
        or container.get("image") != "__DERIVED_IMAGE_DIGEST__"
        or container.get("command") != ["python", "/harness/qualify.py"]
        or container.get("envFrom")
        != [{"secretRef": {"name": "__READ_ONLY_RUNTIME_SECRET__"}}]
    ):
        raise MaterializationError("bundle Job safety baseline drifted")
    return job


def materialize(
    *,
    bundle: Path,
    config: Config,
    bundle_manifest: dict[str, Any],
    local_target: dict[str, str],
    shard: str,
    run_id: str,
    frozen_end: str,
    prior_result_chain_sha256: str,
) -> list[dict[str, Any]]:
    # The reviewed bundle must contain the inert baseline, but no arbitrary
    # template field survives into the runnable resource. Reconstructing the
    # pod closes hidden init-container, host-volume, lifecycle, and env drift.
    _load_job_template(bundle)
    labels = {
        "app.kubernetes.io/name": WORKLOAD_LABEL,
        "futureagi.com/environment": "development",
        "futureagi.com/qualifier-shard": shard,
    }
    metadata = {
        "name": WORKLOAD_NAME,
        "namespace": config.namespace,
        "labels": dict(labels),
        "annotations": {
            "futureagi.com/gcp-project": config.gcp_project,
            "futureagi.com/kube-api-host-sha256": local_target[
                "kube_api_host_sha256"
            ],
            "futureagi.com/kube-context-sha256": hashlib.sha256(
                config.kube_context.encode("utf-8")
            ).hexdigest(),
            "futureagi.com/prior-result-chain-sha256": prior_result_chain_sha256,
            "futureagi.com/read-only-secret-contract-sha256": (
                config.runtime_secret_contract_sha256
            ),
            "futureagi.com/source-manifest-sha256": str(
                bundle_manifest["source_manifest_sha256"]
            ),
        },
    }
    container = {
        "name": "qualify",
        "image": config.derived_image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "/harness/qualify.py"],
        "envFrom": [{"secretRef": {"name": config.runtime_secret}}],
        "env": [],
        "resources": {
            "requests": {"cpu": "2", "memory": "4Gi"},
            "limits": {"cpu": "4", "memory": "8Gi"},
        },
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": 65_532,
            "runAsGroup": 65_532,
            "capabilities": {"drop": ["ALL"]},
        },
        "volumeMounts": [
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "logs", "mountPath": "/app/backend/logs"},
        ],
    }
    fixed_env = {
        "ANNOTATION_SCORE_VALUE_PROJECTION_READ_ENABLED": "false",
        "AWS_EC2_METADATA_DISABLED": "true",
        "CH25_SERVER_ENFORCED_READONLY": "true",
        "CH_DUAL_WRITE": "false",
        "CH_SERVER_ENFORCED_READONLY": "true",
        "CLOUD_DEPLOYMENT": "DEV",
        "DJANGO_CACHE_BACKEND": "locmem",
        "DJANGO_SETTINGS_MODULE": "tfc.settings.settings",
        "ENABLE_INTEGRATIONS": "false",
        "ENV_TYPE": "development",
        "EXPECTED_BASE_COMMIT": str(bundle_manifest["base_commit"]),
        "EXPECTED_IMAGE_DIGEST": config.derived_image,
        "EXPECTED_QUALIFIER_SHA256": str(bundle_manifest["qualifier_sha256"]),
        "EXPECTED_SOURCE_MANIFEST_SHA256": str(
            bundle_manifest["source_manifest_sha256"]
        ),
        "NO_STARTUP_DB_MUTATIONS": "true",
        "PGBOUNCER_READ_HOST": "",
        "PG_DIRECT_HOST": "",
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=9500",
        "PROPERTY_CATALOG_CH_DATABASE": config.catalog_database,
        "PROPERTY_CATALOG_DATABASE": config.catalog_database,
        "PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT": "",
        "PROPERTY_CATALOG_DEV_CATALOG_EPOCH": "",
        "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "",
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE": "",
        "PROPERTY_CATALOG_DEV_ENVIRONMENT": "",
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE": "",
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS": "",
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT": "",
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_USER": "",
        "PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME": "",
        "PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME": "",
        "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID": "",
        "PROPERTY_CATALOG_DEV_IDENTITY": "",
        "PROPERTY_CATALOG_DEV_MAX_WALL_MS": "",
        "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY": "",
        "PROPERTY_CATALOG_DEV_ORGANIZATION_ID": "",
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE": "",
        "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST": "",
        "PROPERTY_CATALOG_DEV_PROJECTION_VERSION": "",
        "PROPERTY_CATALOG_DEV_READ_ACK": CATALOG_ACK,
        "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": "false",
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": "",
        "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY": "",
        "PROPERTY_CATALOG_DEV_SIDECAR_ACK": "",
        "PROPERTY_CATALOG_DEV_SOURCE_DATABASE": "",
        "PROPERTY_CATALOG_DEV_SPAN_SINCE": "",
        "PROPERTY_CATALOG_DEV_SPAN_UNTIL": "",
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": "",
        "PROPERTY_CATALOG_DEV_WORKSPACE_ID": "",
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": "",
        "PROPERTY_CATALOG_DEV_WRITE_CH_HOST": "",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD": "",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PORT": "",
        "PROPERTY_CATALOG_DEV_WRITE_CH_USER": "",
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": ",".join(
            config.workspace_allowlist
        ),
        "PROPERTY_CATALOG_READ_MODE": "read",
        "PYTHONDONTWRITEBYTECODE": "1",
        "QUALIFIER_AUTH_MODE": "direct_existing_principal",
        "QUALIFIER_END_UTC": frozen_end,
        "QUALIFIER_GCP_PROJECT": config.gcp_project,
        "QUALIFIER_KUBE_CONTEXT_SHA256": hashlib.sha256(
            config.kube_context.encode("utf-8")
        ).hexdigest(),
        "QUALIFIER_NAMESPACE": config.namespace,
        "QUALIFIER_PRIOR_RESULT_CHAIN_SHA256": prior_result_chain_sha256,
        "QUALIFIER_READ_ONLY_SECRET_CONTRACT_SHA256": (
            config.runtime_secret_contract_sha256
        ),
        "QUALIFIER_RUN_ID": run_id,
        "QUALIFIER_SERVICE_ACCOUNT": config.service_account,
        "QUALIFIER_SHARD": shard,
        "QUALIFIER_SOS_FORBIDDEN": "true",
        "READ_REPLICA_OPT_IN": "",
        "SENTRY_DSN": "",
        "SERVICE_TYPE": "qualifier",
        "SPAN_ATTRIBUTE_CATALOG_DATABASE": "",
        "SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK": "",
        "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": "false",
        "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "off",
        "STARTUP_DB_MUTATION_MODE": "disabled",
    }
    for name, value in fixed_env.items():
        _set_env(container, name, value)

    pod = {
        "restartPolicy": "Never",
        "serviceAccountName": config.service_account,
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "hostNetwork": False,
        "hostPID": False,
        "hostIPC": False,
        "shareProcessNamespace": False,
        "dnsPolicy": "ClusterFirst",
        "imagePullSecrets": [{"name": config.image_pull_secret}],
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 65_532,
            "runAsGroup": 65_532,
            "fsGroup": 65_532,
            "fsGroupChangePolicy": "OnRootMismatch",
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [container],
        "volumes": [
            {"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}},
            {"name": "logs", "emptyDir": {"sizeLimit": "128Mi"}},
        ],
    }
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": metadata,
        "spec": {
            "activeDeadlineSeconds": 5_400,
            "backoffLimit": 0,
            "parallelism": 1,
            "completions": 1,
            "completionMode": "NonIndexed",
            "template": {
                "metadata": {"labels": dict(labels)},
                "spec": pod,
            },
        },
    }

    service_account = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": config.service_account,
            "namespace": config.namespace,
            "labels": dict(labels),
        },
        "automountServiceAccountToken": False,
        "imagePullSecrets": [{"name": config.image_pull_secret}],
    }
    selector = {"matchLabels": {"app.kubernetes.io/name": WORKLOAD_LABEL}}
    default_deny = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"{WORKLOAD_NAME}-default-deny",
            "namespace": config.namespace,
            "labels": dict(labels),
        },
        "spec": {
            "podSelector": selector,
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [],
        },
    }
    allow_entries: list[dict[str, Any]] = [
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {
                            "kubernetes.io/metadata.name": config.dns_namespace
                        }
                    },
                    "podSelector": {"matchLabels": config.dns_pod_labels},
                }
            ],
            "ports": [
                {"protocol": "UDP", "port": 53},
                {"protocol": "TCP", "port": 53},
            ],
        }
    ]
    for target in config.egress:
        allow_entries.append(
            {
                "to": [{"ipBlock": {"cidr": target.cidr}}],
                "ports": [
                    {"protocol": "TCP", "port": port} for port in target.ports
                ],
            }
        )
    allow_egress = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"{WORKLOAD_NAME}-allow-read-egress",
            "namespace": config.namespace,
            "labels": dict(labels),
        },
        "spec": {
            "podSelector": selector,
            "policyTypes": ["Egress"],
            "egress": allow_entries,
        },
    }
    documents = [service_account, default_deny, allow_egress, job]
    validate_documents(documents, config=config, shard=shard, local_target=local_target)
    return documents


def validate_documents(
    documents: list[dict[str, Any]],
    *,
    config: Config,
    shard: str,
    local_target: dict[str, str],
) -> None:
    identities = [
        (doc.get("kind"), (doc.get("metadata") or {}).get("name"))
        for doc in documents
    ]
    expected = [
        ("ServiceAccount", config.service_account),
        ("NetworkPolicy", f"{WORKLOAD_NAME}-default-deny"),
        ("NetworkPolicy", f"{WORKLOAD_NAME}-allow-read-egress"),
        ("Job", WORKLOAD_NAME),
    ]
    if identities != expected:
        raise MaterializationError("materialized resource inventory or order drifted")
    if any(
        (doc.get("metadata") or {}).get("namespace") != config.namespace
        for doc in documents
    ):
        raise MaterializationError("materialized namespace drifted")
    if any(
        local_target.get(field) != expected
        for field, expected in (
            ("gcp_project", config.gcp_project),
            ("kube_context", config.kube_context),
            ("namespace", config.namespace),
        )
    ):
        raise MaterializationError("materialized target is not the locally verified target")
    job = documents[-1]
    spec = job["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    env = _env_map(container)
    if (
        spec.get("parallelism") != 1
        or spec.get("completions") != 1
        or spec.get("backoffLimit") != 0
        or pod.get("serviceAccountName") != config.service_account
        or pod.get("automountServiceAccountToken") is not False
        or container.get("image") != config.derived_image
        or container.get("envFrom")
        != [{"secretRef": {"name": config.runtime_secret}}]
        or env.get("QUALIFIER_SHARD", {}).get("value") != shard
        or env.get("QUALIFIER_SOS_FORBIDDEN", {}).get("value") != "true"
        or env.get("PROPERTY_CATALOG_READ_MODE", {}).get("value") != "read"
        or env.get("PROPERTY_CATALOG_DEV_RECONCILE_ENABLED", {}).get("value")
        != "false"
    ):
        raise MaterializationError("materialized Job safety contract drifted")
    if any("secret" in doc.get("kind", "").lower() for doc in documents):
        raise MaterializationError("materializer must never emit a Secret")


def _write_private_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        stat.S_IRUSR | stat.S_IWUSR,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _atomic_write_directory(
    output_directory: Path,
    documents: list[dict[str, Any]],
) -> None:
    """Write policies separately so a Job cannot race policy admission."""

    if output_directory.exists() or output_directory.is_symlink():
        raise MaterializationError("output already exists; choose a fresh directory")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".th7247-materialize-", dir=output_directory.parent)
    )
    try:
        _write_private_file(
            temporary / "00-prerequisites.yaml",
            yaml.safe_dump_all(documents[:-1], sort_keys=False).encode("utf-8"),
        )
        _write_private_file(
            temporary / "10-job.yaml",
            yaml.safe_dump_all([documents[-1]], sort_keys=False).encode("utf-8"),
        )
        temporary.replace(output_directory)
    finally:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--gcloud-active-config", required=True, type=Path)
    parser.add_argument("--gcloud-configurations-dir", required=True, type=Path)
    parser.add_argument("--shard", required=True, choices=QUALIFIER_SHARDS)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--frozen-end", required=True)
    parser.add_argument("--prior-result", action="append", type=Path, default=[])
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check == (args.output_directory is not None):
        parser.error("choose exactly one of --check or --output-directory")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not _RUN_ID_RE.fullmatch(args.run_id):
        raise MaterializationError("run id is absent or invalid")
    _parse_frozen_end(args.frozen_end)
    config = load_config(args.config)
    bundle_manifest, _source_manifest = load_bundle(args.bundle)
    local_target = verify_local_target(
        config,
        kubeconfig_path=args.kubeconfig,
        gcloud_active_config_path=args.gcloud_active_config,
        gcloud_configurations_dir=args.gcloud_configurations_dir,
    )
    prior_chain = validate_prior_results(
        args.prior_result,
        shard=args.shard,
        run_id=args.run_id,
        frozen_end=args.frozen_end,
        config=config,
        bundle_manifest=bundle_manifest,
    )
    documents = materialize(
        bundle=args.bundle,
        config=config,
        bundle_manifest=bundle_manifest,
        local_target=local_target,
        shard=args.shard,
        run_id=args.run_id,
        frozen_end=args.frozen_end,
        prior_result_chain_sha256=prior_chain,
    )
    if args.output_directory is not None:
        _atomic_write_directory(args.output_directory, documents)
    else:
        print(
            json.dumps(
                {
                    "action": "offline_validation_only",
                    "gcp_project": config.gcp_project,
                    "kube_context_sha256": hashlib.sha256(
                        config.kube_context.encode("utf-8")
                    ).hexdigest(),
                    "namespace": config.namespace,
                    "resource_count": len(documents),
                    "shard": args.shard,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MaterializationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
