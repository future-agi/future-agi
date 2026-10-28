"""Fixed standalone-runtime-v2 observations and pin-only config rendering.

No image descriptors, release SQL/paths/code, Django imports or data mutations.
A healthy runtime observation is separate from a stopped cold-hold inspection.
Caller maintains runner -> update locks and authenticates the release evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

from .compatibility import Observations, RuntimeImage, decode_document, topology_profile
from .contracts import ReasonCode
from .discovery import DiscoveredRelease
from .docker_host import (bounded_installation, checked_daemon, docker_json, fixed_source)
from .envfile import read_env, render_changes
from .install import VERSION_VARIABLES
from .manifest import hash_path, manifest_hash, parse_image_ref, parse_version
from .orchestrator import GuardRefused, check_binding, fingerprints, require
from .host_facts import safe_path
MOUNTS = {"app": ("/data", "app-data"), "postgres": ("/var/lib/postgresql/data", "postgres-data"),
          "clickhouse": ("/var/lib/clickhouse", "clickhouse-data")}
PIN_KEYS = frozenset(VERSION_VARIABLES)
MAX_ENV = 1024 * 1024
PROBE_ENV = ("PG_DB", "PG_USER", "PG_PASSWORD", "PG_HOST", "PG_PORT", "CH_HOST", "CH_HTTP_PORT",
             "CH_USERNAME", "CH_PASSWORD", "PROPERTY_CATALOG_DATABASE", "REDIS_HOST", "REDIS_PORT",
             "REDIS_PASSWORD", "TEMPORAL_HOST", "STORAGE_BACKEND", "S3_ENDPOINT_URL", "FI_CDC_MODE",
             "EE_LICENSE_KEY", "CLOUD_DEPLOYMENT", "COMPOSE_PROFILES")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_env_bytes(installation):
    path = installation.env_file.resolve(strict=True)
    require(path == installation.env_target, ReasonCode.ENV_CONFLICT)
    with path.open("rb") as handle:
        raw = handle.read(MAX_ENV + 1)
    require(len(raw) <= MAX_ENV, ReasonCode.ENV_CONFLICT)
    return raw


def _private_env(directory, name, raw):
    path = Path(directory) / name
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return path


def _render(installation, env_path, files):
    args = [*installation.compose, "--project-directory", str(installation.root), "--env-file", str(env_path)]
    for path in files:
        args += ["-f", str(path)]
    result = installation.run([*args, "config", "--no-env-resolution", "--format", "json"], check=False)
    require(result.returncode == 0, ReasonCode.CONFIGURATION_CHANGED)
    model = decode_document(result.stdout.encode(), 4 * 1024 * 1024)
    for service in model.get("services", {}).values():
        for entry in service.get("env_file", []):
            require(isinstance(entry, dict) and isinstance(entry.get("path"), str))
            # All other env files would be untracked config inputs. Do not
            # substitute a different file, or silently keep live .env reads.
            require(Path(entry["path"]).resolve() == installation.env_target, ReasonCode.CONFIGURATION_CHANGED)
            entry["path"] = str(env_path)
    # Compose's normalized config serializer already escapes literal dollars
    # for round-tripping. Double-escaping them changes the effective model.
    payload = json.dumps(model, separators=(",", ":"))
    final = [*installation.compose, "--project-directory", str(installation.root),
             "--env-file", str(env_path), "-f", "-", "config"]
    def call(*options):
        response = installation.runner([*final, *options], cwd=str(installation.root),
                                       env=installation.subprocess_env(), input=payload, text=True, capture_output=True)
        require(response.returncode == 0, ReasonCode.CONFIGURATION_CHANGED)
        return response.stdout
    config = decode_document(call("--format", "json").encode(), 4 * 1024 * 1024)
    active = set(call("--services").splitlines())
    require(active <= set(config.get("services", {})) and bool(active))
    config["services"] = {key: value for key, value in config["services"].items() if key in active}
    return config


def fingerprint_for_env(installation, raw_env_bytes) -> str:
    """Exact orchestrator.fingerprints config hash for current or pin-only bytes.

    Private temporary env, two Compose CONFIG passes, no live-env writes, start,
    pull, or source builds. The current-byte reconstruction must equal the real
    current Compose model on EVERY call before a future fingerprint is trusted.
    Non-pin changes, removals, interpolation overrides and env overlays refuse.
    """
    try:
        require(type(raw_env_bytes) is bytes and len(raw_env_bytes) <= MAX_ENV)
        inst = bounded_installation(installation, 60)
        require(inst.mode == "standalone" and not inst.production and not inst.env_overlay,
                ReasonCode.UNSUPPORTED_INSTALLATION)
        inst._check_shell_overrides()
        before = _read_env_bytes(inst)
        files = tuple(inst._ownership_files())
        original = fingerprints(inst)
        with tempfile.TemporaryDirectory(prefix=".fagi-upd-e2e-observe-render-", dir=str(Path.home())) as directory:
            current = _private_env(directory, "current.env", before)
            future = _private_env(directory, "future.env", raw_env_bytes)
            current_env, future_env = read_env(current), read_env(future)
            require(current_env == inst.env and set(current_env) - set(future_env) == set())
            changed = {key: value for key, value in future_env.items() if current_env.get(key) != value}
            require(set(changed) <= PIN_KEYS and not any(key in inst.environ for key in changed))
            for value in changed.values():
                parse_version(value)
            require(render_changes(before.decode("utf-8", "surrogateescape"), changed).encode(
                "utf-8", "surrogateescape") == raw_env_bytes, ReasonCode.CONFIGURATION_CHANGED)
            reconstructed = _render(inst, current, files)
            require(reconstructed == inst.config(refresh=True), ReasonCode.CONFIGURATION_CHANGED)
            predicted = reconstructed if before == raw_env_bytes else _render(inst, future, files)
            proxy = copy.copy(inst)
            proxy.env = future_env
            proxy.config = lambda refresh=False: predicted
            result = fingerprints(proxy)[0]
        require(_read_env_bytes(inst) == before and fingerprints(inst) == original,
                ReasonCode.CONFIGURATION_CHANGED)
        return result
    except GuardRefused:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        raise GuardRefused(ReasonCode.CONFIGURATION_CHANGED) from None


def _probe_document(raw):
    try:
        data = decode_document(raw, 16384)
        require(set(data) == {"schema_version", "contract", "persistence", "migration_inventory_sha256",
                             "applied_migrations_sha256", "active_app_mapping_sha256", "ready"}
                and type(data["schema_version"]) is int and data["schema_version"] == 2
                and data["contract"] == "standalone-runtime-v2"
                and type(data["persistence"]) is dict
                and data["persistence"] == dict(topology_profile("standalone", ()).persistence)
                and all(type(v) is bool for v in data["persistence"].values())
                and data["ready"] is True, ReasonCode.INITIALIZATION_REQUIRED)
        require(all(type(data[k]) is str and re.fullmatch(r"[0-9a-f]{64}", data[k]) for k in
                    ("migration_inventory_sha256", "applied_migrations_sha256", "active_app_mapping_sha256")),
                ReasonCode.MIGRATION_INVENTORY_UNSUPPORTED)
        return data
    except (ValueError, TypeError, KeyError):
        raise GuardRefused(ReasonCode.INITIALIZATION_REQUIRED) from None


def load_migration_mapping():
    """The producer imports the SAME probe APIs/data for its Django cross-check."""
    return load_fixed_data("migration-apps.json")


def probe_source(*, postgres_tables=None, clickhouse_tables=()):
    mapping = load_migration_mapping()
    source = fixed_source("standalone.py")
    # repr(JSON) cannot inject Python statements. Values are reviewed fixed data
    # or validated table identifiers, never a SQL/script/path from a release.
    call = "\nmain(json.loads(" + repr(json.dumps(mapping, sort_keys=True)) + ")"
    if postgres_tables is not None:
        pg, ch = _table_requests(postgres_tables, clickhouse_tables)
        call += ", postgres_tables=" + repr(list(pg)) + ", clickhouse_tables=" + repr(list(ch))
    return source + (call + ")\n").encode()


def _automatic_layout(inst, config):
    contract = load_fixed_data("automatic-layout.json")
    require(contract.get("schema_version") == 1
            and contract.get("runtime_activation_contract") == "standalone-compose-direct-v1"
            and contract.get("automatic_profiles") == [], ReasonCode.UNSUPPORTED_INSTALLATION)
    active = {name: row for name, row in contract["services"].items() if not row["profiles"]}
    require(set(config.get("services", {})) == set(active)
            and set(config.get("volumes", {})) == set(contract["volumes"])
            and set(contract["backup_coverage"]) == set(VOLUME_ROLES), ReasonCode.UNSUPPORTED_INSTALLATION)
    for name, definition in config["services"].items():
        require(not definition.get("build") and not definition.get("profiles")
                and definition.get("restart", "no") in ("unless-stopped", "no")
                and definition.get("network_mode") in (None, ""), ReasonCode.UNSUPPORTED_INSTALLATION)
        actual = definition.get("volumes", [])
        expected = active[name]["mounts"]
        require(len(actual) == len(expected), ReasonCode.UNSUPPORTED_INSTALLATION)
        expected_by_target = {m["target"]: m for m in expected}
        require(len(expected_by_target) == len(expected), ReasonCode.UNSUPPORTED_INSTALLATION)
        for mount in actual:
            want = expected_by_target.get(mount.get("target"))
            require(want is not None and mount.get("type") == want["type"]
                    and mount.get("read_only", False) is want["read_only"], ReasonCode.UNSUPPORTED_INSTALLATION)
            if want["type"] == "volume":
                require(mount.get("source") == want["source"], ReasonCode.UNSUPPORTED_INSTALLATION)
            else:
                # Only the qualified private app secret may select a different
                # file. Its bytes/identity are measured; all other binds are fixed.
                expected_source = str((inst.root / want["source"]).resolve()) if want["source"].startswith("./") else want["source"]
                if contract["bind_coverage"].get(name + ":" + want["target"], {}).get("method") != "saved_private_config":
                    require(mount.get("source") == expected_source, ReasonCode.CONFIGURATION_CHANGED)
    return contract


def _network_roster(inst, binding, records):
    ids = {row["Id"] for row in records.values()}
    networks = {}
    for record in records.values():
        attached = record.get("NetworkSettings", {}).get("Networks", {})
        require(len(attached) == 1, ReasonCode.UNSUPPORTED_INSTALLATION)
        for name, row in attached.items():
            require(re.fullmatch(r"[0-9a-f]{64}", row.get("NetworkID", "")) is not None,
                    ReasonCode.OWNERSHIP_UNCONFIRMED)
            networks[name] = row["NetworkID"]
    require(len(networks) == 1, ReasonCode.UNSUPPORTED_INSTALLATION)
    name, nid = next(iter(networks.items()))
    found = docker_json(inst, "network", "inspect", nid)
    require(len(found) == 1 and found[0]["Id"] == nid and found[0]["Name"] == name
            and found[0].get("Driver") == "bridge"
            and found[0].get("Labels", {}).get("com.docker.compose.project") == binding.compose_project
            and set(found[0].get("Containers", {})) == ids, ReasonCode.OWNERSHIP_UNCONFIRMED)
    # Store identity/roster, not traffic counters or container peer addresses.
    return {"id": nid, "name": name, "driver": "bridge", "containers": sorted(ids)}


def _bind_identity(source):
    import stat
    source = Path(source)
    if source == Path("/dev/null"):
        info = source.lstat()
        require(stat.S_ISCHR(info.st_mode), ReasonCode.CONFIGURATION_CHANGED)
        return manifest_hash({"dev": info.st_dev, "ino": info.st_ino, "rdev": info.st_rdev, "mode": info.st_mode})
    safe_path(source)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_ENV, ReasonCode.CONFIGURATION_CHANGED)
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_ENV + 1)
        after = os.fstat(fd)
        require(len(raw) <= MAX_ENV and (info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), ReasonCode.CONFIGURATION_CHANGED)
        return manifest_hash({"sha256": _sha(raw), "dev": info.st_dev, "ino": info.st_ino,
                              "mode": info.st_mode, "uid": info.st_uid, "gid": info.st_gid})
    finally:
        os.close(fd)


def _volume_identity(volume):
    return {k: volume.get(k) for k in ("Name", "Mountpoint", "Driver", "Options", "CreatedAt", "Scope", "Labels")}


def _canonical_mounts(mounts):
    """Docker's inspect Mounts array is unordered; destinations are unique keys.

    Preserve every mount field in the identity. Only array order is immaterial;
    duplicate/ambiguous targets must never collapse into a dict or set.
    """
    require(type(mounts) is list and all(type(mount) is dict for mount in mounts),
            ReasonCode.UNSUPPORTED_INSTALLATION)
    destinations = [mount.get("Destination") for mount in mounts]
    require(all(type(target) is str and target.startswith("/") for target in destinations)
            and len(destinations) == len(set(destinations)), ReasonCode.UNSUPPORTED_INSTALLATION)
    return sorted(mounts, key=lambda mount: mount["Destination"])


def _published_ports(definition, host, network, *, stopped=False):
    """Compare resolved Compose ports to both configured and live bindings.

    Unspecified host IP means Docker's wildcard families, not permission for a
    loopback-only binding. Explicit addresses must match exactly. Dynamic/range
    ports are unsupported: their stable identity cannot be retained unattended.
    """
    import ipaddress
    expected = {}
    def address(value):
        require(isinstance(value, str), ReasonCode.UNSUPPORTED_INSTALLATION)
        if not value:
            return '*'
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            raise GuardRefused(ReasonCode.UNSUPPORTED_INSTALLATION) from None
    def port(value):
        require(type(value) in (str, int) and str(value).isdigit() and 0 < int(value) <= 65535,
                ReasonCode.UNSUPPORTED_INSTALLATION)
        return str(int(value))
    for item in definition.get('ports') or []:
        require(type(item) is dict and item.get('mode', 'ingress') in ('ingress', 'host'),
                ReasonCode.UNSUPPORTED_INSTALLATION)
        protocol = item.get('protocol', 'tcp')
        require(protocol in ('tcp', 'udp', 'sctp'), ReasonCode.UNSUPPORTED_INSTALLATION)
        key = port(item.get('target')) + '/' + protocol
        expected.setdefault(key, set()).add((address(item.get('host_ip', '')), port(item.get('published'))))
    def bindings(raw):
        require(type(raw) is dict, ReasonCode.CONFIGURATION_CHANGED)
        result = {}
        for key, rows in raw.items():
            if not rows:  # image EXPOSE without a published port
                continue
            require(type(rows) is list and re.fullmatch(r'[0-9]+/(tcp|udp|sctp)', key),
                    ReasonCode.CONFIGURATION_CHANGED)
            result[key] = {(address(row.get('HostIp', '')), port(row.get('HostPort'))) for row in rows}
            require(len(result[key]) == len(rows), ReasonCode.CONFIGURATION_CHANGED)
        return result
    def matches(actual):
        if set(actual) != set(expected):
            return False
        for key, wanted in expected.items():
            for ip, published in actual[key]:
                if (ip, published) not in wanted and ('*', published) not in wanted:
                    return False
                if ('*', published) in wanted and ip not in ('*', '0.0.0.0', '::'):
                    return False
            if any(not any(p == published and (ip == host_ip or ip == '*' and host_ip in ('*', '0.0.0.0', '::'))
                           for host_ip, p in actual[key]) for ip, published in wanted):
                return False
        return True
    configured = bindings(host.get('PortBindings') or {})
    live = bindings(network.get('Ports') or {})
    require(matches(configured) and (stopped or matches(live)), ReasonCode.CONFIGURATION_CHANGED)
    return {'configured': {key: sorted(rows) for key, rows in sorted(configured.items())},
            'live': {key: sorted(rows) for key, rows in sorted(live.items())}}


def _runtime(inst, binding, release, layout, *, stopped=False):
    config = inst.config(refresh=True)
    _automatic_layout(inst, config)
    wanted = dict(layout.products)
    wanted.update({name: None for name in layout.dependencies})
    require(set(config["services"]) == set(wanted), ReasonCode.UNSUPPORTED_INSTALLATION)
    containers = inst.containers()
    require(len(containers) == len(wanted) and {row["service"] for row in containers} == set(wanted)
            and all(not row.get("oneoff") for row in containers), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
    images, identities, volumes, records = {}, {}, {}, {}
    for container in sorted(containers, key=lambda row: row["service"]):
        service = container["service"]
        require(re.fullmatch(r"[0-9a-f]{64}", container["id"]) is not None)
        found = docker_json(inst, "inspect", container["id"])
        require(len(found) == 1)
        record = found[0]
        state, host = record["State"], record["HostConfig"]
        require(record["Id"] == container["id"] and record["Image"] == container["image_id"]
                and not any(state.get(k) for k in ("Paused", "Restarting", "Dead", "OOMKilled")),
                ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        if stopped:
            require(state["Running"] is False and state.get("Status") == "exited"
                    and state.get("ExitCode") == 0, ReasonCode.RECOVERY_REQUIRED)
        else:
            require(container["status"] == "running" and container["health"] == "healthy"
                    and state["Running"] is True and state.get("Health", {}).get("Status") == "healthy",
                    ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        definition = config["services"][service]
        policy = host.get("RestartPolicy", {})
        require(policy.get("Name") in ("unless-stopped", "no")
                and policy.get("Name") == definition.get("restart", "no")
                and policy.get("MaximumRetryCount", 0) == 0
                and type(record.get("RestartCount")) is int and record["RestartCount"] >= 0,
                ReasonCode.UNSUPPORTED_INSTALLATION)
        actual_env = dict(pair.split("=", 1) for pair in record["Config"].get("Env", []) if "=" in pair)
        require(all(actual_env.get(key) == str(value) for key, value in definition.get("environment", {}).items()
                    if value is not None), ReasonCode.CONFIGURATION_CHANGED)
        require(not host.get("Privileged") and host.get("NetworkMode") not in ("host", "none")
                and not host.get("Devices") and host.get("PidMode") not in ("host",), ReasonCode.UNSUPPORTED_INSTALLATION)
        local, selected = inst.image(container["image_id"]), inst.image(definition["image"])
        require(local is not None and selected is not None and local["id"] == selected["id"] == record["Image"],
                ReasonCode.IMAGE_DIGEST_MISMATCH)
        reference = parse_image_ref(definition["image"])
        digests = {parse_image_ref(value).digest for value in local["repo_digests"]
                   if parse_image_ref(value).name == reference.name}
        require(len(digests) == 1 and None not in digests, ReasonCode.IMAGE_DIGEST_MISMATCH)
        dgst = next(iter(digests))
        if wanted[service] is not None and release is not None:
            published = release.manifest["components"][wanted[service]]
            require(reference.name == parse_image_ref(published["image"]).name and dgst == published["digest"],
                    ReasonCode.IMAGE_DIGEST_MISMATCH)
        images[service] = (RuntimeImage(definition["image"], dgst, local["id"]),)
        mounts = _canonical_mounts(record.get("Mounts", []))
        destination, logical = MOUNTS[service]
        selected_mounts = [row for row in mounts if row.get("Destination") == destination]
        require(len(selected_mounts) == 1 and selected_mounts[0].get("Type") == "volume"
                and selected_mounts[0].get("RW") is True, ReasonCode.UNSUPPORTED_INSTALLATION)
        mount = selected_mounts[0]
        declared = config["volumes"][logical]
        require(mount.get("Name") == declared.get("name") and not declared.get("external"))
        volume_rows = docker_json(inst, "volume", "inspect", mount["Name"])
        require(len(volume_rows) == 1)
        volume = volume_rows[0]
        labels = volume.get("Labels") or {}
        require(volume["Name"] == mount["Name"] and volume.get("Mountpoint") == mount.get("Source")
                and volume.get("Driver") == "local" and not volume.get("Options")
                and labels.get("com.docker.compose.project") == binding.compose_project
                and labels.get("com.docker.compose.volume") == logical and bool(volume.get("CreatedAt")),
                ReasonCode.OWNERSHIP_UNCONFIRMED)
        binds = {row["target"]: row for row in definition.get("volumes", []) if row.get("type") == "bind" and row.get("read_only") is True}
        bind_hashes = {}
        require(len(mounts) == 1 + len(binds), ReasonCode.UNSUPPORTED_INSTALLATION)
        for other in mounts:
            if other is mount:
                continue
            target = other.get("Destination", "")
            require(target in binds and other.get("Type") == "bind" and other.get("RW") is False
                    and other.get("Source") == binds[target]["source"], ReasonCode.UNSUPPORTED_INSTALLATION)
            bind_hashes[target] = _bind_identity(other["Source"])
        volumes[service] = manifest_hash({"daemon": binding.daemon_identity, "name": volume["Name"],
            "mountpoint": volume["Mountpoint"], "created_at": volume["CreatedAt"], "driver": volume["Driver"],
            "readonly_config": bind_hashes})
        identities[service] = {"id": record["Id"], "image": record["Image"], "started": state["StartedAt"],
            "mounts": mounts, "config": record["Config"], "restart_count": record["RestartCount"],
            "restart_policy": policy["Name"], "bind_hashes": bind_hashes, "volume": _volume_identity(volume),
            "published_ports": _published_ports(definition, host, record.get('NetworkSettings') or {}, stopped=stopped)}
        records[service] = record
    observed_volumes = owned_volumes(inst, binding)
    require_owned_consumers(inst, observed_volumes.values(), {row["Id"] for row in records.values()})
    if not stopped:
        network = _network_roster(inst, binding, records)
        for identity in identities.values():
            identity["network"] = network
    return images, identities, volumes


from dataclasses import dataclass
from types import MappingProxyType
import time
from .contracts import RuntimeObservationV2, HostSizeObservation, HostTableSize, installation_binding_hash
from .docker_host import (load_fixed_data, owned_volumes, require_owned_consumers, run_readonly_helper,
                          inspect_volume_storage, inspect_backup_storage, VOLUME_ROLES, VolumeFilesystem)


@dataclass(frozen=True, repr=False)
class StandaloneObservation:
    """Private envelope. Baseline owners consume runtime_observation, NOT formats.

    Release-specific migration state is never a cross-version format invariant.
    The original observation timestamp is retained through stopped phases.
    """
    facts: Observations
    env_bytes: bytes
    volumes: object
    runtime_observation: RuntimeObservationV2
    containers: object
    volume_storage_sha256: str
    probe_contract: str = "standalone-runtime-v2"

    def __post_init__(self):
        from dataclasses import replace
        require(isinstance(self.facts, Observations) and isinstance(self.runtime_observation, RuntimeObservationV2)
                and self.facts.runtime_observation == self.runtime_observation
                and self.probe_contract == "standalone-runtime-v2"
                and type(self.env_bytes) is bytes and len(self.env_bytes) <= MAX_ENV
                and self.runtime_observation.env_sha256 == _sha(self.env_bytes)
                and set(self.volumes) == set(topology_profile("standalone", ()).backup_domains)
                and set(self.containers) == set(MOUNTS)
                and re.fullmatch(r"[0-9a-f]{64}", self.volume_storage_sha256), ReasonCode.INITIALIZATION_REQUIRED)
        for name in ("volumes", "containers"):
            object.__setattr__(self, name, _immutable(getattr(self, name)))
        maps = {name: _immutable(getattr(self.facts, name)) for name in (
            "state_epochs", "persistence", "deployment_files", "env", "product_images", "dependency_images")}
        object.__setattr__(self, "facts", replace(self.facts, **maps))

    @property
    def ready(self):
        return self.runtime_observation.ready

    @property
    def structure_sha256(self):
        runtime = self.runtime_observation
        return manifest_hash({"installation": self.installation_sha256,
            "migration_inventory": runtime.migration_inventory_sha256,
            "applied_migrations": runtime.applied_migrations_sha256,
            "active_app_mapping": runtime.active_app_mapping_sha256})

    @property
    def installation_sha256(self):
        runtime = self.runtime_observation
        dependencies = {s: sorted([dict(image=i.image, digest=i.digest, image_id=i.image_id) for i in replicas],
                                   key=lambda i: (i["image"], i["digest"], i["image_id"]))
                        for s, replicas in runtime.dependency_images.items()}
        return manifest_hash({"probe": self.probe_contract, "platform": runtime.platform,
            "volumes": dict(self.volumes), "persistence": dict(self.facts.persistence), "dependencies": dependencies,
            "storage": runtime.storage_sha256})

    @property
    def runtime(self):
        return self.runtime_observation


@dataclass(frozen=True, repr=False)
class StandaloneHostSizeObservation(HostSizeObservation):
    """A6 input retaining both physical allocation and logical restore workload.

    The shared estimator consumes volume_bytes. Supply its conservative workload
    (max(apparent payload, allocated blocks) + per-member metadata), NOT blocks
    alone. Keep the underlying dimensions alongside it for preflight diagnostics.
    A returned observation also proves the currently measured inode capacity can
    hold an extra restored generation. Group by daemon device (not volume name),
    sum every member including roots, and use the lowest free-inode sample. Match
    StandaloneRestore.preflight_capacity's additional 1,024-inode reserve. This is
    a pre-outage check, not a reservation or a replacement for the cold recheck.
    This additive subtype preserves the existing HostSizeObservation interface;
    no legacy/manual estimator or authenticated proof bytes are rewritten.
    """
    volume_storage: tuple = ()

    def __post_init__(self):
        super().__post_init__()
        storage = tuple(self.volume_storage)
        require(len(storage) == len(VOLUME_ROLES) and all(isinstance(v, VolumeFilesystem) for v in storage)
                and {v.role for v in storage} == set(VOLUME_ROLES)
                and dict(self.volume_bytes) == {v.role: v.restore_work_bytes for v in storage},
                ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        groups = {}
        for volume in storage:
            require(type(volume.device) is str and re.fullmatch(r"[0-9]+:[0-9]+", volume.device)
                    and volume.filesystem in ("ext4", "xfs", "btrfs")
                    and type(volume.backing_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", volume.backing_sha256)
                    and type(volume.free_inodes) is int and 0 <= volume.free_inodes <= 9007199254740991,
                    ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            backing = (volume.filesystem, volume.backing_sha256)
            group = groups.setdefault(volume.device, dict(backing=backing, members=0, available=volume.free_inodes))
            require(group["backing"] == backing, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            group["members"] += volume.members
            group["available"] = min(group["available"], volume.free_inodes)
        for group in groups.values():
            require(group["available"] >= group["members"] + 1024, ReasonCode.RESTORE_CAPACITY_INSUFFICIENT)
        object.__setattr__(self, "volume_storage", storage)

    @property
    def volume_allocated_bytes(self):
        return MappingProxyType({v.role: v.allocated_bytes for v in self.volume_storage})

    @property
    def volume_logical_bytes(self):
        return MappingProxyType({v.role: v.logical_bytes for v in self.volume_storage})

    @property
    def volume_members(self):
        return MappingProxyType({v.role: v.members for v in self.volume_storage})


@dataclass(frozen=True, repr=False)
class ColdHoldObservation:
    """Inspection only. The ledger owner constructs/binds the authorised ColdHold."""
    source_observation_sha256: str
    verified_at: datetime
    daemon_identity: str
    storage_sha256: str
    stopped_container_ids: tuple
    restart_counts: object
    restart_policies: object
    contract: str = "standalone-cold-hold-v1"


def _table_requests(postgres_tables, clickhouse_tables):
    result = []
    for tables in (postgres_tables, clickhouse_tables):
        require(type(tables) in (tuple, list) and len(tables) <= 256, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        require(all(type(t) is str and re.fullmatch(r"[a-z_][a-z0-9_]{0,127}\.[a-z_][a-z0-9_]{0,127}", t)
                    for t in tables) and len(tables) == len(set(tables)), ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        result.append(tuple(tables))
    return tuple(result)


class StandaloneStateObserver:
    """Fixed direct-Compose observer. No overlays/runtime rewrites are accepted."""
    def __init__(self, registration, *, timeout=180, stability_period_seconds=None,
                 clock=lambda: datetime.now(timezone.utc), monotonic=time.monotonic, sleeper=time.sleep):
        require((stability_period_seconds is None or (type(stability_period_seconds) is int
                 and 1 <= stability_period_seconds <= 3600)) and 0 < timeout <= 300)
        self.registration, self.timeout, self.clock = registration, timeout, clock
        self.stability_period_seconds, self.monotonic, self.sleeper = stability_period_seconds, monotonic, sleeper

    def _probe(self, inst, binding, app, source):
        volume = next(row["Name"] for row in app["mounts"] if row["Destination"] == "/data")
        actual = dict(pair.split("=", 1) for pair in app["config"].get("Env", []) if "=" in pair)
        environment = {key: actual.get(key, "") for key in PROBE_ENV}
        environment.update(NO_STARTUP_DB_MUTATIONS="true", FI_SKIP_CH25_MIGRATION="1",
            PYTHONDONTWRITEBYTECODE="1", OTEL_SDK_DISABLED="true", SERVICE_TYPE="readonly-observer")
        return run_readonly_helper(inst, binding, image_id=app["image"], source=source,
            volume_name=volume, mount_target="/data", network="container:" + app["id"], environment=environment)

    def observe(self, installation, *, current_release, execution_deployment, boundary):
        try:
            return self._observe(installation, current_release, execution_deployment, boundary)
        except GuardRefused:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            raise GuardRefused(ReasonCode.INITIALIZATION_REQUIRED) from None

    def _observe(self, installation, release, execution, boundary):
        binding = self.registration.binding
        require(isinstance(release, DiscoveredRelease) and release.verification.verified is True, ReasonCode.SIGNATURE_REJECTED)
        require(binding.topology == "standalone" and not binding.production and not binding.profiles,
                ReasonCode.UNSUPPORTED_INSTALLATION)
        # Post-backup/env/rollout guards MUST use retained A plus a cold hold.
        require(boundary in ("enrollment", "before_recovery", "before_backup", "after_rollout", "success", "reconcile"),
                ReasonCode.AUTOMATIC_RECOVERY_PENDING)
        require(set(execution) == {"files"} and bool(execution["files"]), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        # The signed policy budgets stability separately from probe I/O. Keep
        # ONE overall deadline: the aggregate I/O allowance plus that period.
        period = self.stability_period_seconds
        if period is None:
            period = getattr(getattr(installation.runner, "policy", None), "stability_period_seconds", 60)
        require(type(period) is int and 1 <= period <= 3600, ReasonCode.READINESS_FAILED)
        inst = bounded_installation(installation, self.timeout + period)
        check_binding(inst, binding)
        daemon = checked_daemon(inst, binding)
        layout = topology_profile(binding.topology, binding.profiles)
        raw_env = _read_env_bytes(inst)
        config, profile, _ = fingerprints(inst)
        def deployment():
            observed = {name: hash_path(inst.root, name) for name in execution["files"]}
            require(observed == dict(execution["files"]), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
            return observed
        files = deployment()
        require(all(str(path.relative_to(inst.root)) in files for path in inst._ownership_files()), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        images, identities, volumes = _runtime(inst, binding, release, layout)
        storage = inspect_volume_storage(inst, self.registration, helper_image_id=identities["app"]["image"],
                                         timeout=self.timeout, measure_used_bytes=False)
        require(storage.status == "available", ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        data = _probe_document(self._probe(inst, binding, identities["app"], probe_source()))
        require(data["active_app_mapping_sha256"] == manifest_hash(load_migration_mapping()), ReasonCode.MIGRATION_INVENTORY_UNSUPPORTED)
        started = self.monotonic()  # only AFTER the first complete readiness pass
        # Sample HEALTH and exact restart/start/identity counters throughout the
        # bounded stability period, not just at its end. No new startup is used.
        while True:
            require(_runtime(inst, binding, release, layout) == (images, identities, volumes), ReasonCode.CONFIGURATION_CHANGED)
            elapsed = self.monotonic() - started
            if elapsed >= period:
                break
            self.sleeper(min(1, period - elapsed))
        require(_probe_document(self._probe(inst, binding, identities["app"], probe_source())) == data,
                ReasonCode.MIGRATION_INVENTORY_MISMATCH)
        final_storage = inspect_volume_storage(inst, self.registration, helper_image_id=identities["app"]["image"],
                                               timeout=self.timeout, measure_used_bytes=False)
        require(final_storage.status == "available" and final_storage.storage_sha256 == storage.storage_sha256,
                ReasonCode.OWNERSHIP_UNCONFIRMED)
        require(_runtime(inst, binding, release, layout) == (images, identities, volumes), ReasonCode.CONFIGURATION_CHANGED)
        after = checked_daemon(inst, binding)
        require(all(after.get(k) == daemon.get(k) for k in ("ID", "Architecture", "OSType", "Driver", "DriverStatus", "DockerRootDir", "Containerd")),
                ReasonCode.OWNERSHIP_UNCONFIRMED)
        require(deployment() == files and _read_env_bytes(inst) == raw_env and fingerprints(inst)[:2] == (config, profile),
                ReasonCode.CONFIGURATION_CHANGED)
        arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(daemon["Architecture"], daemon["Architecture"])
        platform = daemon["OSType"] + "/" + arch
        require(platform in ("linux/amd64", "linux/arm64"), ReasonCode.UNSUPPORTED_INSTALLATION)
        from .receipt import StateDir
        try:
            StateDir(binding.checkout).journal_path.lstat()
            no_pending = False
        except FileNotFoundError:
            no_pending = True
        now = self.clock()
        products = {s: images[s] for s, _ in layout.products}
        dependencies = {s: images[s] for s in layout.dependencies}
        stores_hash = manifest_hash({"volumes": volumes, "roots": storage.storage_sha256})
        runtime = RuntimeObservationV2(release.version, release.manifest_sha256, now, installation_binding_hash(binding),
            platform, products, dependencies, data["migration_inventory_sha256"], data["applied_migrations_sha256"],
            data["active_app_mapping_sha256"], manifest_hash({"files": files}), _sha(raw_env), config, stores_hash,
            manifest_hash(identities), {r["id"]: r["restart_count"] for r in identities.values()},
            {r["id"]: r["restart_policy"] for r in identities.values()}, True, True)
        facts = Observations(now, platform=platform, product_images=products, dependency_images=dependencies,
            persistence=data["persistence"], deployment_files=files, env=dict(inst.env),
            installation_fingerprint=binding.installation_fingerprint, config_fingerprint=config, profile_fingerprint=profile,
            trust_fingerprint=release.trust_fingerprint, boundary=boundary, owner_confirmed=True, topology_qualified=True,
            runtime_complete=True, no_source_build=True, no_pending_migrations=True, no_pending_transaction=no_pending,
            runtime_observation=runtime, binding_sha256=runtime.binding_sha256)
        domains = {domain: volumes["postgres" if domain == "postgres_app" else "clickhouse" if domain == "clickhouse" else "app"]
                   for domain in layout.backup_domains}
        return StandaloneObservation(facts, raw_env, MappingProxyType(domains), runtime,
                                     _immutable(identities), storage.storage_sha256)

    def observe_cold_hold(self, installation, *, source_observation):
        """Read-only stopped check. No healthy-A probe and no timestamp refresh."""
        try:
            require(isinstance(source_observation, StandaloneObservation), ReasonCode.RECOVERY_REQUIRED)
            source = source_observation.runtime_observation
            inst = bounded_installation(installation, self.timeout)
            binding = self.registration.binding
            check_binding(inst, binding)
            checked_daemon(inst, binding)
            require(installation_binding_hash(binding) == source.binding_sha256, ReasonCode.RECOVERY_REQUIRED)
            expected = source_observation.containers
            def verify_stopped():
                records = inst.containers()
                require({r["id"] for r in records} == set(source.restart_counts), ReasonCode.RECOVERY_REQUIRED)
                for container in records:
                    service = container["service"]
                    require(service in expected and container["id"] == expected[service]["id"], ReasonCode.RECOVERY_REQUIRED)
                    found = docker_json(inst, "inspect", container["id"])
                    require(len(found) == 1, ReasonCode.RECOVERY_REQUIRED)
                    row = found[0]
                    state = row["State"]
                    mounts = _canonical_mounts(row["Mounts"])
                    binds = {m["Destination"]: _bind_identity(m["Source"]) for m in mounts if m["Type"] == "bind"}
                    actual_volume = docker_json(inst, "volume", "inspect", expected[service]["volume"]["Name"])
                    require(len(actual_volume) == 1 and _immutable(_volume_identity(actual_volume[0])) == expected[service]["volume"]
                            and binds == expected[service]["bind_hashes"], ReasonCode.RECOVERY_REQUIRED)
                    require(row["Id"] == container["id"] and row["Image"] == expected[service]["image"]
                            and _immutable(mounts) == expected[service]["mounts"]
                            and _immutable(row["Config"]) == expected[service]["config"]
                            and state.get("StartedAt") == expected[service]["started"]
                            and state["Running"] is False and state.get("Status") == "exited" and state.get("ExitCode") == 0
                            and not any(state.get(k) for k in ("Paused", "Restarting", "Dead", "OOMKilled"))
                            and row.get("RestartCount") == source.restart_counts[container["id"]]
                            and row["HostConfig"]["RestartPolicy"]["Name"] == source.restart_policies[container["id"]],
                            ReasonCode.RECOVERY_REQUIRED)
            verify_stopped()
            storage = inspect_volume_storage(inst, self.registration, helper_image_id=expected["app"]["image"],
                                             timeout=self.timeout, measure_used_bytes=False)
            require(storage.status == "available" and storage.storage_sha256 == source_observation.volume_storage_sha256,
                    ReasonCode.RECOVERY_REQUIRED)
            verify_stopped()
            checked_daemon(inst, binding)
            return ColdHoldObservation(source.sha256, self.clock(), binding.daemon_identity, source.storage_sha256,
                tuple(sorted(source.restart_counts)), source.restart_counts, source.restart_policies)
        except GuardRefused:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            raise GuardRefused(ReasonCode.RECOVERY_REQUIRED) from None

    def measure_update_sizes(self, installation, *, measurements, backup_destination):
        """Baseline-runner A6 API: authenticated TransitionMeasurements in,
        HostSizeObservation out. Sizing is NOT release admission/qualification.
        The destination is the enrolled BackupConfiguration.destination.
        """
        from .contracts import TransitionMeasurements
        try:
            require(isinstance(measurements, TransitionMeasurements), ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            pg = sorted({t.table for m in measurements.migration_measurements if m.engine == "postgres" for t in m.tables})
            ch = sorted({t.table for m in measurements.migration_measurements if m.engine == "clickhouse" for t in m.tables})
            _table_requests(pg, ch)
            inst = bounded_installation(installation, self.timeout)
            binding = self.registration.binding
            check_binding(inst, binding)
            checked_daemon(inst, binding)
            # This is a fixed current-runtime SIZE observation, not a substitute
            # for the separately authenticated source release observation.
            layout = topology_profile(binding.topology, binding.profiles)
            before = _runtime(inst, binding, None, layout)
            sizes, storage = self._measure_sizes(inst, binding, before[1]["app"], pg, ch, backup_destination)
            require(_runtime(inst, binding, None, layout) == before, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            return sizes
        except GuardRefused:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            raise GuardRefused(ReasonCode.RESTORE_CAPACITY_UNKNOWN) from None

    def _measure_sizes(self, inst, binding, app, pg, ch, backup_destination):
        raw = self._probe(inst, binding, app, probe_source(postgres_tables=pg, clickhouse_tables=ch))
        data = decode_document(raw, 128 * 1024)
        require(set(data) == {"schema_version", "table_sizes"} and type(data["schema_version"]) is int
                and data["schema_version"] == 1 and type(data["table_sizes"]) is list, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        tables = tuple(HostTableSize(**row) for row in data["table_sizes"])
        require({(r.engine, r.table) for r in tables} == {("postgres", t) for t in pg} | {("clickhouse", t) for t in ch}
                and len(tables) == len(pg) + len(ch), ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        storage = inspect_volume_storage(inst, self.registration, helper_image_id=app["image"], timeout=self.timeout)
        require(storage.status == "available" and len({v.backing_sha256 for v in storage.volumes}) == 1,
                ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        backup = inspect_backup_storage(backup_destination)
        return (StandaloneHostSizeObservation(tables, {v.role: v.restore_work_bytes for v in storage.volumes},
                min(v.free_bytes for v in storage.volumes), backup["free_bytes"], True,
                volume_storage=storage.volumes), storage)

    def measure_sizes(self, installation, *, source_observation, postgres_tables=(), clickhouse_tables=(),
                      backup_destination):
        """A6 sizes for selected authenticated tables and the ENROLLED destination.

        Caller must bind backup_destination to its private enrollment; it must
        NEVER use a path from a downloaded proof. This method never executes it.
        Disk co-location stays conservative across Docker/client namespaces.
        """
        try:
            pg, ch = _table_requests(postgres_tables, clickhouse_tables)
            require(isinstance(source_observation, StandaloneObservation) and source_observation.ready,
                    ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            inst = bounded_installation(installation, self.timeout)
            binding = self.registration.binding
            check_binding(inst, binding)
            checked_daemon(inst, binding)
            require(installation_binding_hash(binding) == source_observation.runtime.binding_sha256,
                    ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            app = source_observation.containers["app"]
            expected_ids = {r["id"] for r in source_observation.containers.values()}
            require({r["id"] for r in inst.containers()} == expected_ids, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            before = _runtime(inst, binding, None, topology_profile(binding.topology, binding.profiles))
            require(_immutable(before[1]) == source_observation.containers, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            sizes, storage = self._measure_sizes(inst, binding, app, pg, ch, backup_destination)
            require(storage.storage_sha256 == source_observation.volume_storage_sha256
                    and _runtime(inst, binding, None, topology_profile(binding.topology, binding.profiles)) == before,
                    ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            return sizes
        except GuardRefused:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            raise GuardRefused(ReasonCode.RESTORE_CAPACITY_UNKNOWN) from None


def _immutable(value):
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        return MappingProxyType({k: _immutable(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_immutable(v) for v in value)
    return value
