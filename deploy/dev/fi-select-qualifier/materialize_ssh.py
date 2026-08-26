#!/usr/bin/env python3
"""Materialize an inert CATALOG qualifier plan for one exact DEV SSH host.

This module never opens a socket and never invokes ssh, a container runtime,
kubectl, or gcloud. It verifies local identity/attestation files and emits an
argument vector to be reviewed and executed separately on the pinned host.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import fnmatch
import hashlib
import json
import os
import re
import shlex
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

import materialize as common

FORMAT = "futureagi.select-qualifier-dev-ssh"
VERSION = 1
PLAN_SCHEMA = f"{FORMAT}/plan/v1"
WORKLOAD_NAME = "fi-current-select-qualifier-dev"
_TOP_FIELDS = ("format", "version", "host", "runtime", "image", "catalog", "network")
_HOST_FIELDS = (
    "alias",
    "hostname",
    "user",
    "port",
    "host_key_sha256",
    "remote_workdir",
)
_RUNTIME_FIELDS = (
    "container_runtime",
    "container_network",
    "read_only_env_file",
    "read_only_env_contract_sha256",
)
_IMAGE_FIELDS = ("derived",)
_CATALOG_FIELDS = ("database", "workspace_allowlist")
_NETWORK_FIELDS = ("egress_attestation_sha256", "egress")
_FORBIDDEN_SSH_DIRECTIVES = frozenset(
    {
        "proxycommand",
        "proxyjump",
        "localforward",
        "remoteforward",
        "dynamicforward",
        "permitremoteopen",
        "remotecommand",
        "hostkeyalias",
        "identityagent",
        "certificatefile",
        "forwardagent",
    }
)
_REQUIRED_SECRET_KEYS = frozenset(
    {
        "SECRET_KEY",
        "PGBOUNCER_HOST",
        "PGBOUNCER_PORT",
        "PG_DB",
        "PG_USER",
        "PG_PASSWORD",
        "CH25_HOST",
        "CH25_HTTP_PORT",
        "CH25_TCP_PORT",
        "CH25_USER",
        "CH25_PASSWORD",
        "CH25_DATABASE",
        "PROPERTY_CATALOG_CH_HOST",
        "PROPERTY_CATALOG_CH_PORT",
        "PROPERTY_CATALOG_CH_USER",
        "PROPERTY_CATALOG_CH_PASSWORD",
    }
)
_ALLOWED_SECRET_KEYS = _REQUIRED_SECRET_KEYS | frozenset(
    {
        "PG_CONNECT_TIMEOUT_SECONDS",
        "CH_HOST",
        "CH_PORT",
        "CH_USERNAME",
        "CH_PASSWORD",
        "CH_DATABASE",
        "CH_ENABLED",
    }
)
_SECRET_ASSERTIONS = {
    "broker_credentials_absent": True,
    "catalog_clickhouse_server_readonly_2": True,
    "sos_tokens_absent": True,
    "source_and_catalog_identities_distinct": True,
    "source_clickhouse_server_readonly_2": True,
    "postgresql_server_default_read_only": True,
}


@dataclass(frozen=True)
class HostConfig:
    alias: str
    hostname: str
    user: str
    port: int
    host_key_sha256: str
    remote_workdir: str
    container_runtime: str
    container_network: str
    env_file: str
    env_contract_sha256: str
    derived_image: str
    catalog_database: str
    workspace_allowlist: tuple[str, ...]
    egress_attestation_sha256: str
    egress: tuple[common.EgressTarget, ...]


def _physical_file(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise common.MaterializationError(f"{label} is not a physical file")


def _nonzero_sha256(value: Any, label: str) -> str:
    text = common._text(value, label)
    if not common._SHA256_RE.fullmatch(text) or text == "0" * 64:
        raise common.MaterializationError(f"{label} is not a reviewed SHA-256")
    return text


def _remote_path(value: Any, label: str, *, user: str) -> str:
    text = common._text(value, label, dev=True)
    path = PurePosixPath(text)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != text
        or not text.startswith(f"/home/{user}/")
    ):
        raise common.MaterializationError(f"{label} is not an isolated user path")
    return text


def load_config(path: Path) -> HostConfig:
    _physical_file(path, "SSH materializer config")
    raw = common._mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "config")
    common._exact_fields(raw, _TOP_FIELDS, "config")
    if raw["format"] != FORMAT or raw["version"] != VERSION:
        raise common.MaterializationError("SSH config format/version is not current")
    host = common._mapping(raw["host"], "host")
    runtime = common._mapping(raw["runtime"], "runtime")
    image = common._mapping(raw["image"], "image")
    catalog = common._mapping(raw["catalog"], "catalog")
    network = common._mapping(raw["network"], "network")
    common._exact_fields(host, _HOST_FIELDS, "host")
    common._exact_fields(runtime, _RUNTIME_FIELDS, "runtime")
    common._exact_fields(image, _IMAGE_FIELDS, "image")
    common._exact_fields(catalog, _CATALOG_FIELDS, "catalog")
    common._exact_fields(network, _NETWORK_FIELDS, "network")

    alias = common._text(host["alias"], "SSH alias", dev=True)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", alias):
        raise common.MaterializationError("SSH alias is invalid")
    hostname = common._text(host["hostname"], "SSH hostname")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", hostname):
        raise common.MaterializationError("SSH hostname is invalid")
    user = common._text(host["user"], "SSH user")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise common.MaterializationError("SSH user is invalid")
    port = host["port"]
    if isinstance(port, bool) or not isinstance(port, int) or port != 22:
        raise common.MaterializationError("SSH host port must be exactly 22")
    host_key_sha256 = _nonzero_sha256(host["host_key_sha256"], "host-key digest")
    remote_workdir = _remote_path(host["remote_workdir"], "remote workdir", user=user)
    work_tokens = set(re.split(r"[-._/]+", remote_workdir.lower()))
    if not {"fi", "qualifier", "dev"}.issubset(work_tokens):
        raise common.MaterializationError("remote workdir is not purpose-built")

    container_runtime = common._text(runtime["container_runtime"], "container runtime")
    if container_runtime not in {"docker", "podman"}:
        raise common.MaterializationError("container runtime must be docker or podman")
    container_network = common._text(
        runtime["container_network"], "container network", dev=True
    )
    network_tokens = set(re.split(r"[-._]+", container_network.lower()))
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container_network)
        or not {"fi", "qualifier", "dev", "readonly"}.issubset(
            network_tokens
        )
    ):
        raise common.MaterializationError("container network is not purpose-built")
    env_file = _remote_path(runtime["read_only_env_file"], "read-only env file", user=user)
    if PurePosixPath(remote_workdir) not in PurePosixPath(env_file).parents:
        raise common.MaterializationError("read-only env file escaped the remote workdir")
    env_tokens = set(re.split(r"[-._/]+", env_file.lower()))
    if not {"fi", "qualifier", "dev", "readonly"}.issubset(env_tokens):
        raise common.MaterializationError("read-only env file is not purpose-built")
    env_contract_sha256 = _nonzero_sha256(
        runtime["read_only_env_contract_sha256"], "env contract digest"
    )

    derived_image = common._text(image["derived"], "derived image", dev=True)
    if not common._IMAGE_RE.fullmatch(derived_image) or derived_image.endswith("0" * 64):
        raise common.MaterializationError("derived image must have a non-example digest")
    catalog_database = common._text(catalog["database"], "catalog database", dev=True)
    if re.fullmatch(r"fi_catalog_dev_[a-z0-9][a-z0-9_]*", catalog_database) is None:
        raise common.MaterializationError("catalog database is not isolated DEV")
    raw_workspaces = catalog["workspace_allowlist"]
    if not isinstance(raw_workspaces, list) or not 1 <= len(raw_workspaces) <= 32:
        raise common.MaterializationError("workspace allowlist must contain 1..32 UUIDs")
    workspaces = tuple(
        common._uuid4(value, f"workspace_allowlist[{index}]")
        for index, value in enumerate(raw_workspaces)
    )
    if workspaces != tuple(sorted(set(workspaces))):
        raise common.MaterializationError("workspace allowlist must be sorted and unique")

    egress_attestation_sha256 = _nonzero_sha256(
        network["egress_attestation_sha256"], "egress attestation digest"
    )
    raw_egress = network["egress"]
    if not isinstance(raw_egress, list) or not 3 <= len(raw_egress) <= 8:
        raise common.MaterializationError("SSH egress must contain 3..8 exact targets")
    egress: list[common.EgressTarget] = []
    for index, row_value in enumerate(raw_egress):
        row = common._mapping(row_value, f"network.egress[{index}]")
        common._exact_fields(row, common._EGRESS_FIELDS, f"network.egress[{index}]")
        name = common._dns_label(row["name"], f"network.egress[{index}].name")
        purpose = common._text(row["purpose"], f"network.egress[{index}].purpose")
        if purpose not in common._EGRESS_PURPOSES:
            raise common.MaterializationError("SSH egress purpose is not allowlisted")
        try:
            cidr = common.ipaddress.ip_network(str(row["cidr"]), strict=True)
        except ValueError as exc:
            raise common.MaterializationError("SSH egress CIDR is invalid") from exc
        if (
            cidr.version != 4
            or cidr.prefixlen != 32
            or cidr.network_address.is_loopback
            or cidr.network_address.is_link_local
            or cidr.network_address.is_multicast
            or cidr.network_address.is_unspecified
            or cidr.network_address.is_reserved
        ):
            raise common.MaterializationError("SSH egress must be one safe IPv4 /32")
        ports = tuple(row["ports"]) if isinstance(row["ports"], list) else ()
        if (
            not ports
            or any(isinstance(value, bool) or not isinstance(value, int) for value in ports)
            or ports != tuple(sorted(set(ports)))
            or not set(ports).issubset(common._EGRESS_PORTS[purpose])
        ):
            raise common.MaterializationError("SSH egress ports are invalid for purpose")
        egress.append(common.EgressTarget(name, purpose, str(cidr), ports))
    if {row.purpose for row in egress} != common._EGRESS_PURPOSES:
        raise common.MaterializationError("SSH egress coverage is incomplete")
    if tuple((row.purpose, row.cidr, row.ports) for row in egress) != tuple(
        sorted((row.purpose, row.cidr, row.ports) for row in egress)
    ):
        raise common.MaterializationError("SSH egress targets are not canonical")

    return HostConfig(
        alias=alias,
        hostname=hostname,
        user=user,
        port=port,
        host_key_sha256=host_key_sha256,
        remote_workdir=remote_workdir,
        container_runtime=container_runtime,
        container_network=container_network,
        env_file=env_file,
        env_contract_sha256=env_contract_sha256,
        derived_image=derived_image,
        catalog_database=catalog_database,
        workspace_allowlist=workspaces,
        egress_attestation_sha256=egress_attestation_sha256,
        egress=tuple(egress),
    )


def _host_matches(patterns: list[str], alias: str) -> bool:
    positive = False
    for pattern in patterns:
        if pattern.startswith("!") and fnmatch.fnmatchcase(alias, pattern[1:]):
            return False
        if not pattern.startswith("!") and fnmatch.fnmatchcase(alias, pattern):
            positive = True
    return positive


def _ssh_settings(path: Path, alias: str) -> dict[str, Any]:
    """Resolve the small fail-closed OpenSSH subset needed by the plan."""

    values: dict[str, Any] = {"identityfile": []}
    visited: set[Path] = set()
    active = True

    def parse(current: Path) -> None:
        nonlocal active
        current = current.expanduser().resolve(strict=True)
        if current in visited:
            raise common.MaterializationError("SSH config include cycle detected")
        visited.add(current)
        _physical_file(current, "SSH config")
        for raw_line in current.read_text(encoding="utf-8").splitlines():
            try:
                tokens = shlex.split(raw_line, comments=True, posix=True)
            except ValueError as exc:
                raise common.MaterializationError("SSH config syntax is invalid") from exc
            if not tokens:
                continue
            directive = tokens[0].lower()
            arguments = tokens[1:]
            if directive == "match":
                raise common.MaterializationError("SSH Match blocks are not supported")
            if directive == "host":
                active = _host_matches(arguments, alias)
                continue
            if directive == "include" and active:
                import glob

                for pattern in arguments:
                    expanded = pattern.replace("%d", str(Path.home()))
                    if not Path(expanded).is_absolute():
                        expanded = str(current.parent / expanded)
                    matches = sorted(glob.glob(os.path.expanduser(expanded)))
                    if not matches:
                        raise common.MaterializationError("SSH Include matched no files")
                    for match in matches:
                        parse(Path(match))
                continue
            if not active:
                continue
            if directive in _FORBIDDEN_SSH_DIRECTIVES:
                raise common.MaterializationError(
                    f"SSH alias activates forbidden directive: {directive}"
                )
            if directive in {"hostname", "user", "port", "identitiesonly"} and arguments:
                values.setdefault(directive, arguments[0])
            elif directive == "identityfile" and arguments:
                values["identityfile"].append(arguments[0])
        visited.remove(current)

    parse(path)
    return values


def verify_ssh_target(
    config: HostConfig,
    *,
    ssh_config_path: Path,
    known_hosts_path: Path,
) -> dict[str, str]:
    settings = _ssh_settings(ssh_config_path, config.alias)
    try:
        ssh_port = int(settings.get("port", "22"))
    except (TypeError, ValueError) as exc:
        raise common.MaterializationError("SSH alias port is invalid") from exc
    if (
        settings.get("hostname") != config.hostname
        or settings.get("user") != config.user
        or ssh_port != config.port
        or str(settings.get("identitiesonly", "")).lower() != "yes"
    ):
        raise common.MaterializationError("SSH alias target identity drifted")
    identities = settings.get("identityfile")
    if not isinstance(identities, list) or len(identities) != 1:
        raise common.MaterializationError("SSH alias must use one dedicated identity file")
    identity_path = Path(
        identities[0].replace("%d", str(Path.home()))
    ).expanduser()
    _physical_file(identity_path, "SSH identity file")
    if stat.S_IMODE(identity_path.stat().st_mode) & 0o077:
        raise common.MaterializationError("SSH identity file permissions are too broad")

    _physical_file(known_hosts_path, "known_hosts")
    keys: set[tuple[str, str]] = set()
    expected_host = config.hostname if config.port == 22 else f"[{config.hostname}]:{config.port}"
    for raw_line in known_hosts_path.read_text(encoding="utf-8").splitlines():
        tokens = raw_line.split()
        if len(tokens) < 3 or tokens[0].startswith("#"):
            continue
        if expected_host in tokens[0].split(","):
            keys.add((tokens[1], tokens[2]))
    if len(keys) != 1:
        raise common.MaterializationError("known_hosts has no unique exact host key")
    key_type, encoded_key = next(iter(keys))
    if key_type != "ssh-ed25519":
        raise common.MaterializationError("SSH host key must be pinned Ed25519")
    try:
        key_bytes = base64.b64decode(encoded_key, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise common.MaterializationError("known_hosts key is invalid") from exc
    key_sha256 = hashlib.sha256(key_bytes).hexdigest()
    if key_sha256 != config.host_key_sha256:
        raise common.MaterializationError("SSH host-key digest drifted")
    return {
        "alias": config.alias,
        "hostname_sha256": hashlib.sha256(config.hostname.encode()).hexdigest(),
        "host_key_sha256": key_sha256,
        "user": config.user,
    }


def verify_env_contract(path: Path, config: HostConfig) -> dict[str, Any]:
    _physical_file(path, "read-only env contract")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != config.env_contract_sha256:
        raise common.MaterializationError("read-only env contract digest drifted")
    payload = common._mapping(json.loads(raw), "read-only env contract")
    expected_fields = ("schema", "env_file", "keys", "assertions")
    common._exact_fields(payload, expected_fields, "read-only env contract")
    if payload["schema"] != f"{FORMAT}/read-only-env-contract/v1":
        raise common.MaterializationError("read-only env contract schema drifted")
    if payload["env_file"] != config.env_file:
        raise common.MaterializationError("read-only env contract path drifted")
    keys = payload["keys"]
    if (
        not isinstance(keys, list)
        or keys != sorted(set(keys))
        or not all(isinstance(value, str) for value in keys)
        or not _REQUIRED_SECRET_KEYS.issubset(keys)
        or not set(keys).issubset(_ALLOWED_SECRET_KEYS)
    ):
        raise common.MaterializationError("read-only env key inventory is unsafe")
    if payload["assertions"] != _SECRET_ASSERTIONS:
        raise common.MaterializationError("read-only credential assertions drifted")
    return payload


def verify_egress_attestation(path: Path, config: HostConfig) -> dict[str, Any]:
    _physical_file(path, "host egress attestation")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != config.egress_attestation_sha256:
        raise common.MaterializationError("host egress attestation digest drifted")
    payload = common._mapping(json.loads(raw), "host egress attestation")
    expected_fields = ("schema", "host_alias", "default_deny", "dns_restricted", "egress")
    common._exact_fields(payload, expected_fields, "host egress attestation")
    expected_egress = [
        {
            "name": row.name,
            "purpose": row.purpose,
            "cidr": row.cidr,
            "ports": list(row.ports),
        }
        for row in config.egress
    ]
    if (
        payload["schema"] != f"{FORMAT}/host-egress-attestation/v1"
        or payload["host_alias"] != config.alias
        or payload["default_deny"] is not True
        or payload["dns_restricted"] is not True
        or payload["egress"] != expected_egress
    ):
        raise common.MaterializationError("host egress attestation is not exact")
    return payload


def _runtime_environment(
    config: HostConfig,
    bundle_manifest: dict[str, Any],
    *,
    shard: str,
    run_id: str,
    frozen_end: str,
    prior_chain: str,
) -> dict[str, str]:
    values = {
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
        "EXPECTED_SOURCE_MANIFEST_SHA256": str(bundle_manifest["source_manifest_sha256"]),
        "NO_STARTUP_DB_MUTATIONS": "true",
        "PGBOUNCER_READ_HOST": "",
        "PG_DIRECT_HOST": "",
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=9500",
        "PROPERTY_CATALOG_CH_DATABASE": config.catalog_database,
        "PROPERTY_CATALOG_DATABASE": config.catalog_database,
        "PROPERTY_CATALOG_DEV_READ_ACK": common.CATALOG_ACK,
        "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": "false",
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": ",".join(config.workspace_allowlist),
        "PROPERTY_CATALOG_READ_MODE": "read",
        "PYTHONDONTWRITEBYTECODE": "1",
        "QUALIFIER_AUTH_MODE": "direct_existing_principal",
        "QUALIFIER_END_UTC": frozen_end,
        "QUALIFIER_EXECUTION_TARGET": "ssh-host",
        "QUALIFIER_PRIOR_RESULT_CHAIN_SHA256": prior_chain,
        "QUALIFIER_READ_ONLY_SECRET_CONTRACT_SHA256": config.env_contract_sha256,
        "QUALIFIER_RUN_ID": run_id,
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
    for name in (
        "PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT",
        "PROPERTY_CATALOG_DEV_CATALOG_EPOCH",
        "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT",
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE",
        "PROPERTY_CATALOG_DEV_ENVIRONMENT",
        "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID",
        "PROPERTY_CATALOG_DEV_IDENTITY",
        "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY",
        "PROPERTY_CATALOG_DEV_ORGANIZATION_ID",
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE",
        "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST",
        "PROPERTY_CATALOG_DEV_PROJECTION_VERSION",
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE",
        "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY",
        "PROPERTY_CATALOG_DEV_SIDECAR_ACK",
        "PROPERTY_CATALOG_DEV_SOURCE_DATABASE",
        "PROPERTY_CATALOG_DEV_SPAN_SINCE",
        "PROPERTY_CATALOG_DEV_SPAN_UNTIL",
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE",
        "PROPERTY_CATALOG_DEV_WORKSPACE_ID",
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE",
        "PROPERTY_CATALOG_DEV_WRITE_CH_HOST",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PORT",
        "PROPERTY_CATALOG_DEV_WRITE_CH_USER",
    ):
        values[name] = ""
    return dict(sorted(values.items()))


def materialize_plan(
    config: HostConfig,
    bundle_manifest: dict[str, Any],
    target: dict[str, str],
    *,
    shard: str,
    run_id: str,
    frozen_end: str,
    prior_chain: str,
) -> dict[str, Any]:
    environment = _runtime_environment(
        config,
        bundle_manifest,
        shard=shard,
        run_id=run_id,
        frozen_end=frozen_end,
        prior_chain=prior_chain,
    )
    argv = [
        config.container_runtime,
        "run",
        "--rm",
        f"--name={WORKLOAD_NAME}",
        "--pull=never",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=512",
        "--cpus=4",
        "--memory=8g",
        f"--network={config.container_network}",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=536870912",
        "--tmpfs=/app/backend/logs:rw,noexec,nosuid,nodev,size=134217728",
        f"--env-file={config.env_file}",
    ]
    argv.extend(f"--env={name}={value}" for name, value in environment.items())
    argv.append(config.derived_image)
    return {
        "schema": PLAN_SCHEMA,
        "action": "review_only_no_execution",
        "launch_authorized": False,
        "source": {
            "base_commit": bundle_manifest["base_commit"],
            "source_manifest_sha256": bundle_manifest["source_manifest_sha256"],
            "qualifier_sha256": bundle_manifest["qualifier_sha256"],
            "derived_image": config.derived_image,
        },
        "target": target,
        "shard": shard,
        "run_id": run_id,
        "frozen_end": frozen_end,
        "prior_result_chain_sha256": prior_chain,
        "env_contract_sha256": config.env_contract_sha256,
        "egress_attestation_sha256": config.egress_attestation_sha256,
        "environment": environment,
        "container_argv": argv,
        "requires_live_revalidation": [
            "ssh_reachable_with_strict_host_key_checking",
            "derived_digest_present_and_matches_registry",
            "env_file_mode_0400_and_contract_matches",
            "host_default_deny_egress_active_and_exact",
            "postgresql_server_default_read_only",
            "source_clickhouse_server_readonly_2",
            "catalog_clickhouse_server_readonly_2",
        ],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--ssh-config", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--read-only-env-contract", required=True, type=Path)
    parser.add_argument("--egress-attestation", required=True, type=Path)
    parser.add_argument("--shard", required=True, choices=common.QUALIFIER_SHARDS)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--frozen-end", required=True)
    parser.add_argument("--prior-result", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check == (args.output is not None):
        parser.error("choose exactly one of --check or --output")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not common._RUN_ID_RE.fullmatch(args.run_id):
        raise common.MaterializationError("run id is absent or invalid")
    common._parse_frozen_end(args.frozen_end)
    config = load_config(args.config)
    bundle_manifest, _source_manifest = common.load_bundle(args.bundle)
    target = verify_ssh_target(
        config,
        ssh_config_path=args.ssh_config,
        known_hosts_path=args.known_hosts,
    )
    verify_env_contract(args.read_only_env_contract, config)
    verify_egress_attestation(args.egress_attestation, config)
    prior_chain = common.validate_prior_results(
        args.prior_result,
        shard=args.shard,
        run_id=args.run_id,
        frozen_end=args.frozen_end,
        config=config,
        bundle_manifest=bundle_manifest,
    )
    plan = materialize_plan(
        config,
        bundle_manifest,
        target,
        shard=args.shard,
        run_id=args.run_id,
        frozen_end=args.frozen_end,
        prior_chain=prior_chain,
    )
    if args.output is None:
        print(json.dumps({"action": plan["action"], "target": target, "shard": args.shard}, sort_keys=True))
    else:
        if args.output.exists() or args.output.is_symlink():
            raise common.MaterializationError("output already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            args.output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            os.write(
                descriptor,
                json.dumps(plan, sort_keys=True, separators=(",", ":")).encode() + b"\n",
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.MaterializationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
