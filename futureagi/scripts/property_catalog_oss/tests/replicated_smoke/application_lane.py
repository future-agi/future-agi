"""Offline plan and explicitly gated execution of the actual production-mode lane.

No image build/pull, external network, Docker socket in the runner, or production
endpoint is supported. The original two-member diagnostic is left unchanged.
"""

import argparse
import copy
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import harness
from application_base import BASE_IMAGE
from harness import (
    GIB,
    LABEL,
    ROOT,
    RUNS,
    TOKEN_LABEL,
    canonical,
    digest,
    read_file,
    save_new,
)

NODES = ("replica1", "replica2", "replica3")
LANE = "production-application-v1"
MEMORY = {
    **dict.fromkeys(NODES, 3 * GIB),
    "keeper": GIB // 2,
    "postgres": GIB // 2,
    "kafka": 3 * GIB // 2,
    "redis": GIB // 8,
    "runner": 3 * GIB,
}
CPUS = {
    **dict.fromkeys(NODES, "1.5"),
    "keeper": "0.25",
    "postgres": "0.5",
    "kafka": "1.5",
    "redis": "0.25",
    "runner": "2",
}
# Cover every VOLUME declared by the pinned Kafka image. These are private to
# the exact container and consume its existing 1.5 GiB limit, not extra RAM or
# anonymous Docker volumes. Only application children, not Kafka, are restarted.
KAFKA_TMPFS = {
    "/etc/kafka/secrets": "rw,nosuid,nodev,noexec,size=1048576,mode=1777",
    "/mnt/shared/config": "rw,nosuid,nodev,noexec,size=1048576,mode=1777",
    "/var/lib/kafka/data": "rw,nosuid,nodev,noexec,size=268435456,mode=1777",
}
IMAGES = {
    "clickhouse": "sha256:a4e8cf46e526a06f4f52d22f7fa8d07472b55d3c896d7ca3f159e249be38ae62",
    "postgres": "sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
    "kafka": "sha256:bff074a5d0051dbc0bbbcd25b045bb1fe84833ec0d3c7c965d1797dd289ec88f",
    "redis": "sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf",
    "runner": BASE_IMAGE,
}
HELPER = "scripts/property_catalog_oss/tests/managed_smoke/linux_application_runner.py"
SOURCE_SQL = (
    "002_spans_v2.sql",
    "013_attributes_extra_as_string.sql",
    "014_peerdb_is_deleted_back_compat.sql",
)


def validate_manifest(m):
    if type(m) is not dict or set(m) != {
        "lane",
        "run_id",
        "project",
        "token",
        "password",
        "owner_uid",
        "owner_gid",
        "database",
        "candidate_topic",
        "ordered_topic",
        "files",
        "source_files",
    }:
        raise ValueError("unexpected application manifest fields")
    if (
        m["lane"] != LANE
        or not re.fullmatch(r"[0-9a-f]{16}", m["run_id"])
        or m["project"] != "pcapplication-" + m["run_id"]
    ):
        raise ValueError("foreign application project")
    if any(not re.fullmatch(r"[0-9a-f]{64}", m[k]) for k in ("password", "token")):
        raise ValueError("invalid isolated ownership/credential")
    if any(
        type(m[k]) is not int or not 0 < m[k] < 1 << 31
        for k in ("owner_uid", "owner_gid")
    ):
        raise ValueError("runner must have an explicit nonroot owner")
    if (
        m["database"] != "th7247_catalog_prod_" + m["run_id"]
        or m["candidate_topic"] != m["project"] + ".candidates.v2"
        or m["ordered_topic"] != m["project"] + ".ordered.v1"
    ):
        raise ValueError("application destination differs from exact run")
    for field in ("files", "source_files"):
        if type(m[field]) is not dict or len(m[field]) > 20000:
            raise ValueError("invalid bounded file inventory")
        for path, sha in m[field].items():
            p = Path(path)
            if (
                not p.parts
                or p.is_absolute()
                or ".." in p.parts
                or str(p) != path
                or any(part.startswith(".env") for part in p.parts)
                or not re.fullmatch(r"[0-9a-f]{64}", sha)
            ):
                raise ValueError("unsafe application file reference")
            if field == "source_files" and (
                len(p.parts) < 2
                or p.parts[0] not in {"futureagi", "fi-collector"}
                or "runs" in p.parts
            ):
                raise ValueError("foreign source snapshot reference")


def xml_files(m):
    # Reuse the same server/Keeper limits, replacing only the fixture's explicit
    # infrastructure inventory. Production table DDL is never translated here.
    original = harness.xml_files(m)
    base = ET.fromstring(original["replica1.xml"])
    shard = base.find("remote_servers/smoke_cluster/shard")
    third = copy.deepcopy(shard.findall("replica")[0])
    third.find("host").text = "replica3"
    shard.append(third)
    result = {k: v for k, v in original.items() if not k.startswith("replica")}
    for name in NODES:
        root = copy.deepcopy(base)
        root.find("interserver_http_host").text = name
        root.find("macros/replica").text = name
        result[name + ".xml"] = ET.tostring(root) + b"\n"
    return result


def configuration(m):
    validate_manifest(m)
    directory = RUNS / m["project"]
    labels = {LABEL: m["run_id"], TOKEN_LABEL: m["token"]}
    services = {}
    for name, memory in MEMORY.items():
        image = IMAGES["clickhouse" if name in (*NODES, "keeper") else name]
        service = {
            "image": image,
            "platform": "linux/arm64",
            "pull_policy": "never",
            "restart": "no",
            "container_name": m["project"] + "-" + name,
            "hostname": m["project"] + "-" + name,
            "labels": labels,
            "mem_limit": memory,
            "memswap_limit": memory,
            "cpus": CPUS[name],
            "pids_limit": 512,
            "security_opt": ["no-new-privileges:true"],
            "logging": {
                "driver": "json-file",
                "options": {"max-size": "5m", "max-file": "2"},
            },
            "ports": [],
        }
        if name in NODES:
            service.update(
                environment={"CLICKHOUSE_SKIP_USER_SETUP": "1"},
                tmpfs=["/var/log/clickhouse-server:rw,size=67108864,uid=101,gid=101"],
                depends_on=["keeper"],
                volumes=[
                    f"{name}-data:/var/lib/clickhouse",
                    f"{directory}/{name}.xml:/etc/clickhouse-server/config.d/smoke.xml:ro",
                    f"{directory}/users.xml:/etc/clickhouse-server/users.d/smoke.xml:ro",
                    f"{directory}/backend/.ci/clickhouse-storage-policy.xml:/etc/clickhouse-server/config.d/storage-policy.xml:ro",
                    f"{directory}/backend/tracer/services/clickhouse/v2/schema:/schema:ro",
                ],
            )
        elif name == "keeper":
            service.update(
                user="clickhouse:clickhouse",
                entrypoint=[
                    "/usr/bin/clickhouse",
                    "keeper",
                    "--config",
                    "/etc/keeper.xml",
                ],
                tmpfs=["/var/log/clickhouse-server:rw,size=67108864,uid=101,gid=101"],
                volumes=[
                    "keeper-data:/var/lib/clickhouse",
                    f"{directory}/keeper.xml:/etc/keeper.xml:ro",
                ],
            )
        elif name == "postgres":
            service.update(
                environment={
                    "POSTGRES_USER": "smoke_admin",
                    "POSTGRES_PASSWORD": m["password"],
                    "POSTGRES_DB": "managed_smoke",
                },
                volumes=[
                    "postgres-data:/var/lib/postgresql/data",
                    f"{directory}/backend/scripts/property_catalog_oss:/bootstrap:ro",
                ],
            )
        elif name == "kafka":
            service.update(
                hostname="kafka",
                environment={
                    "CLUSTER_ID": "MkU3OEVBNTcwNTJENDM2Qk",
                    "KAFKA_NODE_ID": "1",
                    "KAFKA_PROCESS_ROLES": "broker,controller",
                    "KAFKA_LISTENERS": "INTERNAL://:9092,CONTROLLER://:9093",
                    "KAFKA_ADVERTISED_LISTENERS": "INTERNAL://kafka:9092",
                    "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT",
                    "KAFKA_INTER_BROKER_LISTENER_NAME": "INTERNAL",
                    "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
                    "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@kafka:9093",
                    "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
                    "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR": "1",
                    "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR": "1",
                    "KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS": "0",
                    "KAFKA_AUTO_CREATE_TOPICS_ENABLE": "false",
                    "KAFKA_HEAP_OPTS": "-Xms256m -Xmx512m",
                    "KAFKA_LOG_DIRS": "/var/lib/kafka/data",
                    "KAFKA_LOG_RETENTION_HOURS": "1",
                    "KAFKA_LOG_RETENTION_BYTES": "67108864",
                    "KAFKA_MESSAGE_MAX_BYTES": "1048576",
                    "KAFKA_REPLICA_FETCH_MAX_BYTES": "1048576",
                },
                tmpfs=[path + ":" + options for path, options in KAFKA_TMPFS.items()],
            )
        elif name == "redis":
            service.update(
                command=["redis-server", "--save", "", "--appendonly", "no"],
                tmpfs=["/data:rw,size=16777216"],
            )
        else:
            service.update(
                profiles=["application"],
                user=f"{m['owner_uid']}:{m['owner_gid']}",
                read_only=True,
                cap_drop=["ALL"],
                working_dir="/fixture/backend",
                entrypoint=[
                    "python",
                    "-B",
                    "/fixture/backend/" + HELPER,
                    "execute",
                    "/fixture",
                ],
                environment={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"},
                tmpfs=[
                    "/tmp:rw,nosuid,size=134217728,mode=1777",
                    "/fixture/backend/tfc/logs:rw,nosuid,noexec,size=67108864,"
                    f"uid={m['owner_uid']},gid={m['owner_gid']},mode=0700",
                ],
                volumes=[
                    f"{directory}:/fixture:rw",
                    f"{directory}/backend:/fixture/backend:ro",
                    f"{directory}/go-source:/fixture/go-source:ro",
                    f"{directory}/bin:/fixture/bin:ro",
                ],
            )
        services[name] = service
    return {
        "name": m["project"],
        "services": services,
        "networks": {
            "default": {
                "name": m["project"] + "-network",
                "internal": True,
                "labels": labels,
            }
        },
        "volumes": {
            name + "-data": {
                "name": m["project"] + "-" + name + "-data",
                "labels": labels,
            }
            for name in (*NODES, "keeper", "postgres")
        },
    }


def source_files():
    paths = (
        subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "futureagi",
                "fi-collector",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    result, total = {}, 0
    for name in sorted(set(paths) - {""}):
        path = Path(name)
        if any(
            p.startswith(".env") or p in {"runs", "__pycache__"} for p in path.parts
        ):
            continue
        source = ROOT / path
        if not source.exists():
            continue
        if source.is_symlink() or source.resolve() != source.absolute():
            raise ValueError("source snapshot refuses symlinks")
        raw = read_file(source, 32 << 20)
        total += len(raw)
        if total > 512 << 20 or len(result) >= 20000:
            raise ValueError("source snapshot exceeds bounded inventory")
        result[name] = digest(raw)
    return result


def schema(m):
    from tracer.services.clickhouse.v2.catalog_prod_schema import (
        render_catalog_prod_schema,
    )

    manifest = render_catalog_prod_schema(
        target_database=m["database"],
        cluster="smoke_cluster",
        keeper_path_prefix="/clickhouse/tables",
    )
    return {"statements": list(manifest.statements), "sha256": manifest.manifest_sha256}


def plan():
    run_id = secrets.token_hex(8)
    m = {
        "lane": LANE,
        "run_id": run_id,
        "project": "pcapplication-" + run_id,
        "token": secrets.token_hex(32),
        "password": secrets.token_hex(32),
        "owner_uid": os.getuid(),
        "owner_gid": os.getgid(),
        "database": "th7247_catalog_prod_" + run_id,
        "candidate_topic": "pcapplication-" + run_id + ".candidates.v2",
        "ordered_topic": "pcapplication-" + run_id + ".ordered.v1",
        "files": {},
        "source_files": source_files(),
    }
    validate_manifest(m)
    RUNS.mkdir(mode=0o700, exist_ok=True)
    if RUNS.is_symlink():
        raise ValueError("runs directory cannot be a symlink")
    directory = RUNS / m["project"]
    directory.mkdir(mode=0o700)
    for name, sha in m["source_files"].items():
        source = ROOT / name
        destination = (
            directory
            / ("backend" if name.startswith("futureagi/") else "go-source")
            / Path(name).relative_to(Path(name).parts[0])
        )
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if digest(read_file(destination, 32 << 20)) != sha:
            raise ValueError("source changed during snapshot; discard this plan")
    (directory / "bin").mkdir(mode=0o700)
    # Docker must find the nested mountpoint before mounting the source read-only.
    (directory / "backend/tfc/logs").mkdir(mode=0o700, parents=True, exist_ok=True)
    files = {
        **xml_files(m),
        "compose.json": canonical(configuration(m)),
        "schema.json": canonical(schema(m)),
    }
    for name, raw in files.items():
        save_new(directory / name, raw)
        if name.endswith(".xml"):
            (directory / name).chmod(0o644)
        m["files"][name] = digest(raw)
    save_new(directory / "manifest.json", m)
    return directory, m


def load(directory, *, sources=True):
    directory = Path(directory).absolute()
    if directory.parent != RUNS or directory.is_symlink() or RUNS.is_symlink():
        raise ValueError("application run must be an exact owned child")
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError("application run must be private and owned")
    raw = read_file(directory / "manifest.json")
    m = json.loads(raw)
    validate_manifest(m)
    if (
        raw != canonical(m)
        or directory.name != m["project"]
        or m["owner_uid"] != os.getuid()
        or m["owner_gid"] != os.getgid()
        or (directory / "manifest.json").stat().st_mode & 0o077
    ):
        raise ValueError("noncanonical application manifest")
    expected = {**xml_files(m), "compose.json": canonical(configuration(m))}
    if set(m["files"]) != {*expected, "schema.json"}:
        raise ValueError("application file inventory changed")
    for name, sha in m["files"].items():
        raw = read_file(directory / name)
        if (
            (name in expected and raw != expected[name])
            or digest(raw) != sha
            or (directory / name).stat().st_mode & 0o022
        ):
            raise ValueError("application plan bytes changed")
    if sources:
        for name, sha in m["source_files"].items():
            prefix = "backend" if name.startswith("futureagi/") else "go-source"
            local = directory / prefix / Path(name).relative_to(Path(name).parts[0])
            if local.resolve() != local or digest(read_file(local, 32 << 20)) != sha:
                raise ValueError("frozen application source changed")
    return m


class ApplicationDocker(harness.Docker):
    def resources(self):
        doc = configuration(self.m)
        return (
            [("container", s["container_name"]) for s in doc["services"].values()]
            + [("volume", v["name"]) for v in doc["volumes"].values()]
            + [("network", n["name"]) for n in doc["networks"].values()]
        )

    def connect(self):
        super().connect()
        if self.host != "unix:///Users/nikhilpareek/.colima/default/docker.sock":
            raise RuntimeError(
                "application lane requires the coordinated local Colima endpoint"
            )

    @property
    def compose(self):
        return [
            "compose",
            "--env-file",
            "/dev/null",
            "--project-directory",
            str(self.directory),
            "-p",
            self.m["project"],
            "-f",
            str(self.directory / "compose.json"),
        ]

    def preflight(self, confirmation):
        if confirmation != self.m["run_id"]:
            raise ValueError("exact coordinated budget confirmation required")
        if any(self.inspect(k, n) is not None for k, n in self.resources()):
            raise ValueError("refusing preexisting application resources")
        info = json.loads(self.command(["info", "--format", "{{json .}}"]).stdout)
        ids = self.command(["ps", "-q"]).stdout.split()
        rows = (
            json.loads(self.command(["container", "inspect", *ids]).stdout)
            if ids
            else []
        )
        caps = [r["HostConfig"].get("Memory", 0) for r in rows]
        requested = sum(MEMORY.values())
        if (
            any(c <= 0 for c in caps)
            or sum(caps) + requested + 2 * GIB > info["MemTotal"]
        ):
            raise RuntimeError("application headroom unproven; no cap override")
        cpu_caps = [r["HostConfig"].get("NanoCpus", 0) for r in rows]
        requested_cpu = sum(
            int(float(value) * 1_000_000_000) for value in CPUS.values()
        )
        if (
            any(cap <= 0 for cap in cpu_caps)
            or sum(cpu_caps) + requested_cpu > info["NCPU"] * 1_000_000_000
        ):
            raise RuntimeError("application CPU headroom unproven; no cap override")
        images = {}
        image_metadata = {}
        for name, reference in IMAGES.items():
            image = self.inspect("image", reference)
            if image is None or (image["Os"], image["Architecture"]) != (
                "linux",
                "arm64",
            ):
                raise RuntimeError(
                    "required pinned Linux arm64 image absent; no pull permitted"
                )
            images[name] = image["Id"]
            image_metadata[name] = image
        for service, spec in configuration(self.m)["services"].items():
            image = image_metadata[
                "clickhouse" if service in (*NODES, "keeper") else service
            ]
            destinations = {v.split(":")[1] for v in spec.get("volumes", [])}
            destinations.update(v.split(":")[0] for v in spec.get("tmpfs", []))
            if set(image.get("Config", {}).get("Volumes") or {}) - destinations:
                raise RuntimeError("image would allocate unplanned anonymous volumes")
        return {
            "requested_cap": requested,
            "reserve": 2 * GIB,
            "existing_caps": caps,
            "docker_memory": info["MemTotal"],
            "requested_cpu_nanos": requested_cpu,
            "existing_cpu_nanos": cpu_caps,
            "docker_cpus": info["NCPU"],
            "images": images,
        }

    def owned(self, *, running=False):
        # Base cleanup checks exact names/IDs/labels. Runtime checks below use
        # this lane's inventory, never the two-member diagnostic's inventory.
        rows = super().owned()
        intent_path = self.directory / "start-intent.json"
        images = (
            json.loads(read_file(intent_path))["images"] if intent_path.exists() else {}
        )
        for kind, _, row in rows:
            if kind == "network" and not row.get("Internal"):
                raise RuntimeError("application network is not private")
            if kind != "container":
                continue
            service = row["Config"]["Labels"].get("com.docker.compose.service")
            expected = configuration(self.m)["services"].get(service)
            image = "clickhouse" if service in (*NODES, "keeper") else service
            if (
                expected is None
                or row["Config"]["Image"] != expected["image"]
                or image not in images
                or row["Image"] != images[image]
            ):
                raise RuntimeError(
                    "application container image/identity differs from start intent"
                )
        if running:
            self.verify_running()
        return rows

    def verify_running(self):
        """Check concrete resources before any fixture connection or new stage."""
        rows = self.owned()
        for kind, _, row in rows:
            if kind != "container":
                continue
            service = row["Config"]["Labels"]["com.docker.compose.service"]
            spec = configuration(self.m)["services"][service]
            host = row["HostConfig"]
            completed_runner = service == "runner" and row["State"].get("ExitCode") == 0
            if (
                (not row["State"]["Running"] and not completed_runner)
                or row["State"].get("OOMKilled")
                or row.get("RestartCount", 0)
                or host["Memory"] != spec["mem_limit"]
                or host["MemorySwap"] != spec["memswap_limit"]
                or host["NanoCpus"] != int(float(spec["cpus"]) * 1e9)
                or host["PidsLimit"] != 512
                or host.get("Privileged")
                or any(row["NetworkSettings"]["Ports"].values())
                or set(row["NetworkSettings"]["Networks"])
                != {self.m["project"] + "-network"}
                or "no-new-privileges" not in " ".join(host.get("SecurityOpt") or [])
            ):
                raise RuntimeError(
                    "application runtime resource/security cap differs from plan"
                )
            if service == "runner" and (
                not host["ReadonlyRootfs"]
                or host["CapDrop"] != ["ALL"]
                or row["Config"]["User"] != spec["user"]
            ):
                raise RuntimeError("Linux runner privilege differs from plan")
            expected_tmpfs = dict(v.split(":", 1) for v in spec.get("tmpfs", []))
            actual_tmpfs = host.get("Tmpfs") or {}
            if set(actual_tmpfs) != set(expected_tmpfs) or any(
                set(actual_tmpfs[path].split(",")) != set(options.split(","))
                for path, options in expected_tmpfs.items()
            ):
                raise RuntimeError(
                    "application tmpfs paths or size caps differ from plan"
                )

    def exec_owned(self, service, arguments, *, input=None, timeout=60):
        self.verify_running()
        matches = [
            (name, row)
            for kind, name, row in self.owned()
            if kind == "container"
            and row["Config"]["Labels"].get("com.docker.compose.service") == service
        ]
        if len(matches) != 1 or not matches[0][1]["State"]["Running"]:
            raise ValueError("exact owned service is not running")
        command = [
            "docker",
            "--host",
            self.host,
            "container",
            "exec",
            "-i",
            matches[0][1]["Id"],
            *arguments,
        ]
        return subprocess.run(
            command,
            input=input,
            text=True,
            capture_output=True,
            env=self.env,
            cwd=self.directory,
            timeout=timeout,
            check=True,
        )


def prepare(directory, m, confirmation):
    if confirmation != m["run_id"]:
        raise ValueError("exact coordinated budget confirmation required")
    # Native host compiler only; no image build, package install or network.
    env = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "GOCACHE", "GOMODCACHE")
        if k in os.environ
    }
    env.update(
        GOOS="linux",
        GOARCH="arm64",
        CGO_ENABLED="0",
        GOPROXY="off",
        GOSUMDB="off",
        GOTOOLCHAIN="local",
    )
    binaries = {}
    for name in (
        "fi-property-catalog-sequencer",
        "fi-property-catalog-consumer",
        "fi-collector",
    ):
        path = directory / "bin" / name
        if path.exists():
            raise ValueError("refusing to replace an existing frozen binary")
        subprocess.run(
            ["go", "build", "-o", str(path), "./cmd/" + name],
            cwd=directory / "go-source",
            env=env,
            timeout=180,
            check=True,
            capture_output=True,
        )
        binaries[name] = digest(read_file(path, 256 << 20))
    save_new(directory / "binaries.json", binaries)


def execute(directory, m, confirmation):
    docker = ApplicationDocker(directory, m)
    docker.connect()
    budget = docker.preflight(confirmation)
    binaries = json.loads(read_file(directory / "binaries.json"))
    if set(binaries) != {
        "fi-property-catalog-sequencer",
        "fi-property-catalog-consumer",
        "fi-collector",
    }:
        raise ValueError("incomplete frozen Linux executables")
    for name, sha in binaries.items():
        if digest(read_file(directory / "bin" / name, 256 << 20)) != sha:
            raise ValueError("frozen Linux executable changed")
    save_new(directory / "start-intent.json", budget)
    try:
        docker.command(
            docker.compose + ["up", "-d", "--no-build", "--pull", "never"], timeout=60
        )
        deadline = time.monotonic() + 90
        while True:
            try:
                for node in NODES:
                    docker.exec_owned(
                        node, ["clickhouse-client", "--query", "SELECT 1"], timeout=5
                    )
                docker.exec_owned(
                    "postgres",
                    ["pg_isready", "-U", "smoke_admin", "-d", "managed_smoke"],
                    timeout=5,
                )
                docker.exec_owned(
                    "kafka",
                    [
                        "/opt/kafka/bin/kafka-topics.sh",
                        "--bootstrap-server",
                        "kafka:9092",
                        "--list",
                    ],
                    timeout=10,
                )
                break
            except (subprocess.SubprocessError, ValueError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        for name in SOURCE_SQL:
            docker.exec_owned(
                "replica1",
                ["clickhouse-client", "--database", "default", "--multiquery"],
                input=read_file(
                    directory / "backend/tracer/services/clickhouse/v2/schema" / name
                ).decode(),
            )
        docker.exec_owned(
            "postgres",
            [
                "env",
                "PGHOST=127.0.0.1",
                "PGUSER=smoke_admin",
                "PGDATABASE=managed_smoke",
                "PGPASSWORD=" + m["password"],
                "sh",
                "/bootstrap/bootstrap_postgres.sh",
            ],
        )
        docker.exec_owned(
            "postgres",
            [
                "psql",
                "-U",
                "smoke_admin",
                "-d",
                "managed_smoke",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            input="CREATE ROLE pc_collector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            f"NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD '{m['password']}';\n"
            "GRANT pg_read_all_data TO pc_collector;\n"
            "ALTER ROLE pc_collector SET default_transaction_read_only='on';\n"
            "ALTER ROLE pc_collector SET statement_timeout='3000ms';\n",
        )
        for topic in (m["candidate_topic"], m["ordered_topic"]):
            docker.exec_owned(
                "kafka",
                [
                    "/opt/kafka/bin/kafka-topics.sh",
                    "--bootstrap-server",
                    "kafka:9092",
                    "--create",
                    "--topic",
                    topic,
                    "--partitions",
                    "1",
                    "--replication-factor",
                    "1",
                    "--config",
                    "retention.ms=3600000",
                    "--config",
                    "retention.bytes=67108864",
                ],
            )
        docker.command(
            docker.compose
            + [
                "--profile",
                "application",
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "runner",
            ],
            timeout=30,
        )
        docker.verify_running()
        deadline = time.monotonic() + 900
        name = m["project"] + "-runner"
        while True:
            resources = docker.owned()
            runner = next(
                row for kind, n, row in resources if kind == "container" and n == name
            )
            if not runner["State"]["Running"]:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("bounded application runner deadline expired")
            time.sleep(1)
        if runner["State"].get("OOMKilled") or runner["State"]["ExitCode"] != 0:
            raise RuntimeError(
                "application runner failed; retained logs are diagnostic only"
            )
        evidence = json.loads(
            read_file(directory / "replicated-application-result.json")
        )
        if (
            evidence.get("status") != "passed"
            or evidence.get("run_id") != m["run_id"]
            or evidence.get("environment") != "production"
            or len(set(evidence.get("members", []))) != 3
            or evidence.get("stages")
            != ["empty-installation", "initial", "ingested", "restarted"]
        ):
            raise RuntimeError("application did not publish successful evidence")
        from application_evidence import qualify

        result = {**evidence, "kafka_to_three_members": qualify(docker, directory, m)}
        save_new(directory / "qualified-application-result.json", result)
        return result
    finally:
        try:
            for kind, name, row in docker.owned():
                if kind == "container":
                    logs = docker.command(
                        ["container", "logs", "--tail", "1000", row["Id"]], check=False
                    )
                    save_new(
                        directory / (name + ".log"),
                        (logs.stdout + logs.stderr)[-2_000_000:].encode(),
                    )
        finally:
            docker.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "prepare", "execute", "cleanup"))
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("--confirm-budget")
    args = parser.parse_args()
    if args.action == "plan":
        directory, m = plan()
        print(
            json.dumps(
                {
                    "directory": str(directory),
                    "run_id": m["run_id"],
                    "container_memory_gib": sum(MEMORY.values()) / GIB,
                    "reserve_gib": 2,
                    "status": "planned_no_docker",
                }
            )
        )
        return
    if args.directory is None:
        parser.error("exact application directory required")
    m = load(args.directory, sources=args.action != "cleanup")
    if args.action == "cleanup":
        docker = ApplicationDocker(args.directory, m)
        docker.connect()
        docker.cleanup()
    else:
        {"prepare": prepare, "execute": execute}[args.action](
            args.directory, m, args.confirm_budget
        )


if __name__ == "__main__":
    main()
