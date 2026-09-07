"""Plan-only by default; explicit, locally owned Docker resources only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
RUNS = HERE / "runs"
LABEL = "futureagi.replicated-smoke.run"
TOKEN_LABEL = "futureagi.replicated-smoke.token"
PREFIX = "pcreplicated-"
IMAGE = "clickhouse/clickhouse-server:25.3-alpine"
GIB = 1 << 30
MEMORY = {"replica1": 3 * GIB, "replica2": 3 * GIB, "keeper": GIB // 2}
MAX_BYTES = 4 << 20
sys.path.insert(0, str(ROOT / "futureagi"))


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(path, limit=MAX_BYTES):
    """Never follow a final symlink, block on a FIFO, or read unbounded data."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("not a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("file exceeds byte limit")
        return raw
    finally:
        os.close(fd)


def save_new(path, raw):
    """Exclusive, fsynced evidence: never overwrite an unresolved run/write."""
    if not isinstance(raw, bytes):
        raw = canonical(raw)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def reserve_ports():
    sockets, ports = [], {}
    try:
        for key in (
            "replica1_http",
            "replica1_native",
            "replica2_http",
            "replica2_native",
            "keeper",
        ):
            sock = socket.socket()
            sockets.append(sock)
            sock.bind(("127.0.0.1", 0))
            ports[key] = sock.getsockname()[1]
        return ports
    finally:
        for sock in sockets:
            sock.close()


def validate_manifest(m):
    if set(m) != {
        "version",
        "run_id",
        "token",
        "project",
        "ports",
        "password",
        "files",
        "source_files",
    }:
        raise ValueError("unexpected manifest fields")
    if (
        type(m["version"]) is not int
        or m["version"] != 1
        or re.fullmatch(r"[a-f0-9]{16}", m["run_id"]) is None
    ):
        raise ValueError("invalid run identity")
    if m["project"] != PREFIX + m["run_id"]:
        raise ValueError("foreign project name")
    if any(
        re.fullmatch(r"[a-f0-9]{64}", m[key]) is None for key in ("token", "password")
    ):
        raise ValueError("invalid ownership token or isolated credential")
    expected = {
        "replica1_http",
        "replica1_native",
        "replica2_http",
        "replica2_native",
        "keeper",
    }
    if (
        set(m["ports"]) != expected
        or len(set(m["ports"].values())) != 5
        or any(
            type(port) is not int or not 1024 < port < 65536
            for port in m["ports"].values()
        )
    ):
        raise ValueError("invalid/distinct loopback port inventory")


def configuration(m):
    labels = {LABEL: m["run_id"], TOKEN_LABEL: m["token"]}
    directory = RUNS / m["project"]
    services = {}
    for name, memory in MEMORY.items():
        service = {
            "image": IMAGE,
            "pull_policy": "never",
            "restart": "no",
            "container_name": f"{m['project']}-{name}",
            "hostname": f"{m['project']}-{name}",
            "labels": labels,
            "mem_limit": memory,
            "memswap_limit": memory,
            "cpus": "0.5" if name == "keeper" else "2",
            "pids_limit": 512,
            "security_opt": ["no-new-privileges:true"],
            "logging": {
                "driver": "json-file",
                "options": {"max-size": "5m", "max-file": "2"},
            },
            "volumes": [f"{name}-data:/var/lib/clickhouse"],
        }
        if name == "keeper":
            service.update(
                entrypoint=[
                    "/usr/bin/clickhouse",
                    "keeper",
                    "--config",
                    "/etc/keeper.xml",
                ],
                user="clickhouse:clickhouse",
                ports=[f"127.0.0.1:{m['ports']['keeper']}:9181"],
            )
            service["volumes"].append(f"{directory}/keeper.xml:/etc/keeper.xml:ro")
        else:
            service["environment"] = {"CLICKHOUSE_SKIP_USER_SETUP": "1"}
            service["ports"] = [
                f"127.0.0.1:{m['ports'][name + '_' + key]}:{port}"
                for key, port in (("http", 8123), ("native", 9000))
            ]
            service["volumes"] += [
                f"{directory}/{name}.xml:/etc/clickhouse-server/config.d/smoke.xml:ro",
                f"{directory}/users.xml:/etc/clickhouse-server/users.d/smoke.xml:ro",
            ]
            service["depends_on"] = ["keeper"]
        services[name] = service
    return {
        "name": m["project"],
        "services": services,
        "networks": {
            "default": {
                "name": m["project"] + "-network",
                # Docker Desktop does not publish host ports on an internal
                # network. Use a fresh bridge, with loopback-only bindings.
                "driver": "bridge",
                "labels": labels,
            }
        },
        "volumes": {
            f"{name}-data": {"name": f"{m['project']}-{name}-data", "labels": labels}
            for name in MEMORY
        },
    }


def xml_files(m):
    cluster = "".join(
        f"<replica><host>{name}</host><port>9000</port><user>smoke_probe</user>"
        f"<password>{m['password']}</password></replica>"
        for name in ("replica1", "replica2")
    )
    files = {}
    for name in ("replica1", "replica2"):
        files[name + ".xml"] = f"""<clickhouse>
<listen_host>0.0.0.0</listen_host><interserver_http_host>{name}</interserver_http_host>
<max_server_memory_usage>2147483648</max_server_memory_usage><max_connections>64</max_connections>
<max_concurrent_queries>8</max_concurrent_queries><background_pool_size>16</background_pool_size>
<background_schedule_pool_size>8</background_schedule_pool_size><background_fetches_pool_size>4</background_fetches_pool_size>
<background_message_broker_schedule_pool_size>4</background_message_broker_schedule_pool_size>
<background_distributed_schedule_pool_size>4</background_distributed_schedule_pool_size>
<logger><level>warning</level><console>true</console></logger>
<zookeeper><node><host>keeper</host><port>9181</port></node></zookeeper>
<macros><shard>1</shard><replica>{name}</replica></macros>
<storage_configuration><disks><cold_local><type>local</type><path>/var/lib/clickhouse/cold/</path></cold_local></disks>
<policies><tiered><volumes><hot><disk>default</disk></hot><cold><disk>cold_local</disk></cold></volumes></tiered></policies></storage_configuration>
<remote_servers replace="replace"><smoke_cluster><shard><internal_replication>true</internal_replication>{cluster}</shard></smoke_cluster></remote_servers>
</clickhouse>\n""".encode()
    files["keeper.xml"] = b"""<clickhouse><listen_host>0.0.0.0</listen_host>
<logger><level>warning</level><console>true</console></logger>
<keeper_server><tcp_port>9181</tcp_port><server_id>1</server_id>
<log_storage_path>/var/lib/clickhouse/coordination/log</log_storage_path>
<snapshot_storage_path>/var/lib/clickhouse/coordination/snapshots</snapshot_storage_path>
<coordination_settings><operation_timeout_ms>10000</operation_timeout_ms><session_timeout_ms>30000</session_timeout_ms><raft_logs_level>warning</raft_logs_level></coordination_settings>
<raft_configuration><server><id>1</id><hostname>keeper</hostname><port>9234</port></server></raft_configuration>
</keeper_server></clickhouse>\n"""
    password_hash = digest(m["password"].encode())
    files["users.xml"] = f"""<clickhouse><profiles>
<smoke_admin><max_threads>2</max_threads><max_memory_usage>268435456</max_memory_usage></smoke_admin>
<smoke_probe><readonly>2</readonly><max_threads>1</max_threads><max_memory_usage>268435456</max_memory_usage><max_execution_time>10</max_execution_time></smoke_probe>
</profiles><users>
<default><networks replace="replace"><ip>127.0.0.1</ip><ip>::1</ip></networks></default>
<smoke_admin><password_sha256_hex>{password_hash}</password_sha256_hex><networks><ip>::/0</ip></networks><profile>smoke_admin</profile><quota>default</quota><access_management>1</access_management></smoke_admin>
<smoke_probe><password_sha256_hex>{password_hash}</password_sha256_hex><networks><ip>::/0</ip></networks><profile>smoke_probe</profile><quota>default</quota></smoke_probe>
</users></clickhouse>\n""".encode()
    return files


def plan():
    from schema_probe import schema_bundle, source_fingerprints

    m = {
        "version": 1,
        "run_id": secrets.token_hex(8),
        "token": secrets.token_hex(32),
        "password": secrets.token_hex(32),
        "ports": reserve_ports(),
        "files": {},
        "source_files": source_fingerprints(),
    }
    m["project"] = PREFIX + m["run_id"]
    validate_manifest(m)
    RUNS.mkdir(mode=0o700, exist_ok=True)
    if RUNS.is_symlink():
        raise ValueError("runs directory must not be a symlink")
    directory = RUNS / m["project"]
    directory.mkdir(mode=0o700)
    files = {
        **xml_files(m),
        "compose.json": canonical(configuration(m)),
        "schema.json": canonical(schema_bundle(m)),
    }
    for name, raw in files.items():
        save_new(directory / name, raw)
        if name.endswith(".xml"):
            # Bind-mounted configs must be readable by image UID clickhouse.
            # The enclosing 0700 run directory still protects host credentials.
            (directory / name).chmod(0o644)
        m["files"][name] = digest(raw)
    save_new(directory / "manifest.json", m)
    return directory, m


def load(directory):
    directory = Path(directory).absolute()
    if directory.parent != RUNS or directory.is_symlink() or RUNS.is_symlink():
        raise ValueError(
            "run must be a direct non-symlink child of the owned runs directory"
        )
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError("run directory must be private and owned by the current user")
    if (directory / "manifest.json").lstat().st_mode & 0o077:
        raise ValueError("manifest must be private")
    raw = read_file(directory / "manifest.json")
    m = json.loads(raw)
    validate_manifest(m)
    if raw != canonical(m) or directory.name != m["project"]:
        raise ValueError("noncanonical/foreign manifest")
    expected = {**xml_files(m), "compose.json": canonical(configuration(m))}
    if set(m["files"]) != {*expected, "schema.json"}:
        raise ValueError("unexpected file inventory")
    for name, sha in m["files"].items():
        actual = read_file(directory / name)
        if (directory / name).lstat().st_mode & 0o022:
            raise ValueError("run files must not be group/world writable")
        if digest(actual) != sha or (name in expected and expected[name] != actual):
            raise ValueError(f"changed run file: {name}")
    return m


class Docker:
    def __init__(self, directory, manifest):
        self.directory, self.m = directory, manifest
        # Do not inherit COMPOSE_*, DOCKER_HOST, project credentials or .env.
        self.env = {
            key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ
        }
        self.host = None

    def command(self, args, *, timeout=30, check=True):
        prefix = ["docker"] + (["--host", self.host] if self.host else [])
        return subprocess.run(
            prefix + args,
            cwd=self.directory,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=check,
        )

    def connect(self):
        context = json.loads(self.command(["context", "inspect"]).stdout)
        host = context[0]["Endpoints"]["docker"]["Host"]
        if not host.startswith("unix:///"):
            raise RuntimeError("only a local Unix-socket Docker engine is permitted")
        self.host = host

    def resources(self):
        expected = [("container", f"{self.m['project']}-{name}") for name in MEMORY]
        expected += [("volume", f"{self.m['project']}-{name}-data") for name in MEMORY]
        return expected + [("network", self.m["project"] + "-network")]

    def inspect(self, kind, name):
        result = self.command([kind, "inspect", name], check=False)
        if result.returncode:
            absent = {
                "container": [f"no such object: {name}", f"no such container: {name}"],
                "volume": [f"get {name}: no such volume"],
                "network": [f"network {name} not found", f"no such network: {name}"],
                "image": [f"no such image: {name}"],
            }
            if not any(
                message.lower() in result.stderr.lower() for message in absent[kind]
            ):
                raise RuntimeError(f"cannot establish {kind} absence: {result.stderr}")
            return None
        rows = json.loads(result.stdout)
        if len(rows) != 1:
            raise RuntimeError("ambiguous Docker resource")
        return rows[0]

    def owned(self, *, running=False):
        resources = []
        for kind, name in self.resources():
            row = self.inspect(kind, name)
            if row is None:
                if running:
                    raise RuntimeError(f"owned {kind} is absent: {name}")
                continue
            labels = (
                row.get("Config", {}).get("Labels", {})
                if kind == "container"
                else row.get("Labels", {})
            )
            if (
                labels.get(LABEL) != self.m["run_id"]
                or labels.get(TOKEN_LABEL) != self.m["token"]
                or labels.get("com.docker.compose.project") != self.m["project"]
            ):
                raise RuntimeError(f"refusing foreign ownership: {kind} {name}")
            if row.get("Name", "").lstrip("/") != name:
                raise RuntimeError("resource name is not the exact planned name")
            if str(row.get("Id", "")).startswith("c2854450c0d8"):
                raise RuntimeError("explicitly protected preexisting container")
            if kind == "container" and running:
                service = labels.get("com.docker.compose.service")
                if (
                    service not in MEMORY
                    or not row["State"]["Running"]
                    or row["State"].get("OOMKilled")
                ):
                    raise RuntimeError(
                        "missing/misidentified/running/OOM container evidence"
                    )
                if (
                    row["HostConfig"]["Memory"] != MEMORY[service]
                    or row["HostConfig"]["MemorySwap"] != MEMORY[service]
                ):
                    raise RuntimeError("container memory cap differs from plan")
                expected_ports = (
                    {"9181/tcp": self.m["ports"]["keeper"]}
                    if service == "keeper"
                    else {
                        "8123/tcp": self.m["ports"][service + "_http"],
                        "9000/tcp": self.m["ports"][service + "_native"],
                    }
                )
                ports = {k: v for k, v in row["NetworkSettings"]["Ports"].items() if v}
                if ports != {
                    k: [{"HostIp": "127.0.0.1", "HostPort": str(v)}]
                    for k, v in expected_ports.items()
                }:
                    raise RuntimeError("not the planned owned loopback endpoints")
            resources.append((kind, name, row))
        return resources

    def up(self, confirmed):
        if confirmed != self.m["run_id"]:
            raise RuntimeError(
                "explicit coordinated-headroom run-id confirmation required"
            )
        from schema_probe import schema_bundle, source_fingerprints

        if self.m["source_files"] != source_fingerprints():
            raise RuntimeError("product/schema inputs changed; create a fresh plan")
        if read_file(self.directory / "schema.json") != canonical(
            schema_bundle(self.m)
        ):
            raise RuntimeError("schema does not match the pinned plan")
        if any(self.inspect(kind, name) is not None for kind, name in self.resources()):
            raise RuntimeError(
                "startup refuses every preexisting resource, including owned names"
            )
        info = json.loads(self.command(["info", "--format", "{{json .}}"]).stdout)
        ids = self.command(["ps", "-q"]).stdout.split()
        current = (
            json.loads(self.command(["container", "inspect", *ids]).stdout)
            if ids
            else []
        )
        # Charge full configured limits, not momentary idle RSS. An uncapped
        # preexisting container prevents automatic admission (never stop it).
        caps = [row["HostConfig"].get("Memory", 0) for row in current]
        headroom = {
            "docker_memory": info["MemTotal"],
            "existing_caps": caps,
            "requested_cap": sum(MEMORY.values()),
            "reserve": 2 * GIB,
        }
        save_new(self.directory / "headroom.json", headroom)
        if (
            any(cap <= 0 for cap in caps)
            or sum(caps) + sum(MEMORY.values()) + 2 * GIB > info["MemTotal"]
        ):
            raise RuntimeError(
                "headroom unproven: existing uncapped workloads or insufficient capped budget"
            )
        sockets = []
        try:
            for port in self.m["ports"].values():
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(("127.0.0.1", port))
        finally:
            for sock in sockets:
                sock.close()
        image = self.inspect("image", IMAGE)
        if image is None:
            raise RuntimeError(
                "CH25.3 image absent; arrange its public image pull separately, then use a fresh plan"
            )
        save_new(
            self.directory / "image.json",
            {"id": image["Id"], "digests": image.get("RepoDigests", [])},
        )
        save_new(
            self.directory / "start-intent.json",
            {"run_id": self.m["run_id"], "outcome": "starting"},
        )
        self.command(
            [
                "compose",
                "--env-file",
                "/dev/null",
                "--project-directory",
                str(self.directory),
                "-p",
                self.m["project"],
                "-f",
                str(self.directory / "compose.json"),
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
            ],
            timeout=60,
        )

    def cleanup(self):
        # Validate every target before the first removal. No compose down,
        # label wildcard deletion, prune, image removal, or recursive host rm.
        resources = self.owned()
        for kind, name, row in resources:
            current = self.inspect(kind, name)
            labels = (
                (current or {}).get("Config", {}).get("Labels", {})
                if kind == "container"
                else (current or {}).get("Labels", {})
            )
            if (
                labels.get(LABEL) != self.m["run_id"]
                or labels.get(TOKEN_LABEL) != self.m["token"]
                or labels.get("com.docker.compose.project") != self.m["project"]
            ):
                raise RuntimeError("cleanup ownership changed")
            if current != row:
                # Container stats/state may evolve; identity must not.
                if current is None or current.get("Id", current.get("Name")) != row.get(
                    "Id", row.get("Name")
                ):
                    raise RuntimeError("cleanup resource changed identity")
            if kind == "container":
                self.command(["container", "rm", "--force", row["Id"]])
            elif kind == "volume":
                self.command(["volume", "rm", name])
            else:
                self.command(["network", "rm", row["Id"]])
        if any(self.inspect(kind, name) is not None for kind, name in self.resources()):
            raise RuntimeError("owned resources remain after cleanup")
        save_new(
            self.directory / ("cleanup-" + secrets.token_hex(4) + ".json"),
            {
                "owned_resources_absent": True,
                "test_data_removed": True,
                "evidence_retained": True,
            },
        )
