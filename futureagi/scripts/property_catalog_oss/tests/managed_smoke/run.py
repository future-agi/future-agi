#!/usr/bin/env python3
"""Disposable live infrastructure/parity smoke. Never uses the root Compose file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
LABEL = "futureagi.managed-smoke.run"
PREFIX = "pcmanaged-"
TRANSPORT_RUN_TIMEOUT_SECONDS = 900
APPLICATION_RUN_TIMEOUT_SECONDS = 2400
IMAGES = {
    "clickhouse": "clickhouse/clickhouse-server:25.3-alpine",
    "postgres": "postgres:16-alpine",
    "kafka": "apache/kafka:4.1.0",
    "collector": "gcr.io/distroless/static-debian12:nonroot",
    "redis": "redis:7-alpine",
}


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def reserve_ports() -> dict[str, int]:
    sockets = []
    try:
        result = {}
        for name in ("http", "native", "postgres", "kafka", "otlp"):
            sock = socket.socket()
            sockets.append(sock)
            sock.bind(("127.0.0.1", 0))
            result[name] = sock.getsockname()[1]
        return result
    finally:
        for sock in sockets:
            sock.close()


def compose_document(manifest: dict) -> dict:
    run_id, ports = manifest["run_id"], manifest["ports"]
    labels = {LABEL: run_id}
    schema = ROOT / "futureagi/tracer/services/clickhouse/v2/schema"
    bootstrap = ROOT / "futureagi/scripts/property_catalog_oss"

    def service(image: str, memory: str, cpus: str) -> dict:
        return {
            "image": image,
            "restart": "no",
            "labels": labels,
            "mem_limit": memory,
            "cpus": cpus,
            "pids_limit": 512,
            "logging": {
                "driver": "json-file",
                "options": {"max-size": "5m", "max-file": "2"},
            },
        }

    ch = service(IMAGES["clickhouse"], "3g", "2")
    ch.update(
        {
            "environment": {"CLICKHOUSE_SKIP_USER_SETUP": "1"},
            "ports": [
                f"127.0.0.1:{ports['http']}:8123",
                f"127.0.0.1:{ports['native']}:9000",
            ],
            "volumes": [
                "clickhouse-data:/var/lib/clickhouse",
                f"{ROOT}/futureagi/.ci/clickhouse-storage-policy.xml:/etc/clickhouse-server/config.d/storage-policy.xml:ro",
                f"{HERE}/clickhouse-limits.xml:/etc/clickhouse-server/config.d/managed-smoke.xml:ro",
                f"{schema}:/schema:ro",
                f"{bootstrap}:/bootstrap:ro",
            ],
            "healthcheck": {
                "test": ["CMD", "wget", "--spider", "-q", "http://127.0.0.1:8123/ping"],
                "interval": "2s",
                "timeout": "3s",
                "retries": 60,
            },
        }
    )
    pg = service(IMAGES["postgres"], "512m", "1")
    pg.update(
        {
            "environment": {
                "POSTGRES_USER": "smoke_admin",
                "POSTGRES_PASSWORD": manifest["password"],
                "POSTGRES_DB": "managed_smoke",
            },
            "ports": [f"127.0.0.1:{ports['postgres']}:5432"],
            "volumes": [
                "postgres-data:/var/lib/postgresql/data",
                f"{bootstrap}:/bootstrap:ro",
            ],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U smoke_admin -d managed_smoke"],
                "interval": "2s",
                "timeout": "3s",
                "retries": 60,
            },
        }
    )
    kafka = service(IMAGES["kafka"], "1536m", "2")
    kafka.update(
        {
            "hostname": "kafka",
            "ports": [f"127.0.0.1:{ports['kafka']}:29092"],
            "environment": {
                "CLUSTER_ID": manifest["cluster_id"],
                "KAFKA_NODE_ID": "1",
                "KAFKA_PROCESS_ROLES": "broker,controller",
                "KAFKA_LISTENERS": "INTERNAL://:9092,EXTERNAL://:29092,CONTROLLER://:9093",
                "KAFKA_ADVERTISED_LISTENERS": f"INTERNAL://kafka:9092,EXTERNAL://127.0.0.1:{ports['kafka']}",
                "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT",
                "KAFKA_INTER_BROKER_LISTENER_NAME": "INTERNAL",
                "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
                "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@kafka:9093",
                "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
                "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR": "1",
                "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR": "1",
                "KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS": "0",
                "KAFKA_AUTO_CREATE_TOPICS_ENABLE": "false",
                "KAFKA_HEAP_OPTS": "-Xms256m -Xmx512m",
                "KAFKA_LOG_DIRS": "/tmp/kraft-data",
                "KAFKA_LOG_RETENTION_HOURS": "1",
                "KAFKA_LOG_RETENTION_BYTES": "67108864",
                "KAFKA_MESSAGE_MAX_BYTES": "1048576",
                "KAFKA_REPLICA_FETCH_MAX_BYTES": "1048576",
            },
            "tmpfs": ["/tmp/kraft-data:rw,size=268435456,mode=1777"],
            "healthcheck": {
                "test": [
                    "CMD-SHELL",
                    "/opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --list >/dev/null",
                ],
                "interval": "5s",
                "timeout": "10s",
                "retries": 30,
                "start_period": "10s",
            },
        }
    )
    services = {"clickhouse": ch, "postgres": pg, "kafka": kafka}
    if manifest.get("application"):
        # Current collector binary in its normal distroless base. Only OTLP is
        # published, on a unique loopback port; the fixed admin listener stays
        # inside this newly owned network. This is not a release-image build.
        collector = service(manifest["project"] + "-collector:local", "512m", "1")
        collector.update(
            {
                "profiles": ["ingestion"],
                "pull_policy": "never",
                "entrypoint": ["/usr/local/bin/fi-collector"],
                "command": ["-config", "/etc/fi-collector/config.yaml"],
                "user": "65532:65532",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "ports": [f"127.0.0.1:{ports['otlp']}:4318"],
                "tmpfs": [
                    "/tmp:rw,size=16777216,mode=1777",
                    "/var/lib/fi-collector:rw,size=16777216,mode=0700,uid=65532,gid=65532",
                ],
                "environment": {
                    "FI_CH_URL": "http://clickhouse:8123",
                    "FI_CH_DATABASE": "default",
                    "FI_CH_USERNAME": "managed_smoke_ingest",
                    "FI_CH_PASSWORD": manifest["password"],
                    # Existing project only: the auth path needs no PG writes.
                    "FI_PG_WRITE": f"postgres://managed_smoke_collector:{manifest['password']}@postgres:5432/managed_smoke?sslmode=disable",
                    "FI_PG_READ": f"postgres://managed_smoke_collector:{manifest['password']}@postgres:5432/managed_smoke?sslmode=disable",
                    "FI_AUTH_REDIS_ADDR": "redis:6379",
                    "FI_GRPC_ADDR": ":4317",
                    "FI_HTTP_ADDR": ":4318",
                    "FI_CATALOG_MODE": "disabled",
                    "FI_PROPERTY_CATALOG_MODE": "kafka",
                    "FI_PROPERTY_CATALOG_ENVIRONMENT": "development",
                    "FI_PROPERTY_CATALOG_DEV_ACK": "PROPERTY_CATALOG_V1_DEV_ONLY",
                    "FI_PROPERTY_CATALOG_KAFKA_BROKERS": "kafka:9092",
                    "FI_PROPERTY_CATALOG_KAFKA_TOPIC": manifest["candidate_topic"]
                    + ".application",
                    "FI_PROPERTY_CATALOG_SPOOL_DIR": "/var/lib/fi-collector/property-candidates",
                },
            }
        )
        redis = service(IMAGES["redis"], "128m", "0.5")
        redis.update(
            {
                "profiles": ["ingestion"],
                "command": ["redis-server", "--save", "", "--appendonly", "no"],
                "ports": [],
                "tmpfs": ["/data:rw,size=16777216"],
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "ping"],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 30,
                },
            }
        )
        services.update(collector=collector, redis=redis)
    return {
        "name": manifest["project"],
        "services": services,
        "volumes": {
            name: {"labels": labels} for name in ("clickhouse-data", "postgres-data")
        },
        "networks": {"default": {"labels": labels}},
    }


class Run:
    def __init__(self, directory: Path, manifest: dict, timeout: int | None = None):
        self.directory, self.manifest = directory, manifest
        if timeout is None:
            timeout = (
                APPLICATION_RUN_TIMEOUT_SECONDS
                if manifest.get("application")
                else TRANSPORT_RUN_TIMEOUT_SECONDS
            )
        self.deadline = time.monotonic() + timeout
        self.index = 0
        self.log_prefix = f"{os.getpid()}-{secrets.token_hex(4)}"
        # Do not inherit project credentials, .env paths, compose profiles or overrides.
        allowed = (
            "PATH",
            "HOME",
            "TMPDIR",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
            "GOPATH",
            "GOMODCACHE",
            "GOCACHE",
        )
        self.env = {name: os.environ[name] for name in allowed if name in os.environ}
        self.env.update(
            {"PYTHONDONTWRITEBYTECODE": "1", "COMPOSE_DISABLE_ENV_FILE": "1"}
        )
        self.compose = [
            "docker",
            "compose",
            "--project-name",
            manifest["project"],
            "--project-directory",
            str(directory),
            "--env-file",
            str(directory / "empty.env"),
            "-f",
            str(directory / "compose.json"),
        ]

    def command(
        self,
        args: list[str],
        *,
        timeout: int = 60,
        stdin: str | None = None,
        check: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("managed smoke aggregate deadline expired")
        self.index += 1
        result = subprocess.run(
            args,
            input=stdin,
            text=True,
            capture_output=True,
            env=self.env,
            cwd=cwd or self.directory,
            timeout=min(timeout, remaining),
        )
        output = result.stdout + result.stderr
        log_name = f"{self.log_prefix}-{self.index:03d}.log"
        (self.directory / log_name).write_text(output[-2_000_000:])
        if check and result.returncode:
            raise RuntimeError(
                f"{args[0]} failed ({result.returncode}); see {log_name}: {output[-1600:]}"
            )
        return result

    def owned(self) -> list[str]:
        ids = self.command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={self.manifest['project']}",
            ]
        ).stdout.split()
        if ids:
            containers = json.loads(self.command(["docker", "inspect", *ids]).stdout)
            for container in containers:
                if container["Config"]["Labels"].get(LABEL) != self.manifest["run_id"]:
                    raise RuntimeError(
                        "refusing operation on a container without this run's ownership token"
                    )
        return ids

    def execute(
        self, service: str, args: list[str], **kwargs
    ) -> subprocess.CompletedProcess:
        if service not in IMAGES or not self.owned():
            raise RuntimeError("exec requires this run's owned service")
        return self.command(self.compose + ["exec", "-T", service, *args], **kwargs)

    def cleanup(self) -> None:
        self.deadline = time.monotonic() + 120
        self.owned()
        for kind in ("volume", "network"):
            names = self.command(
                [
                    "docker",
                    kind,
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={self.manifest['project']}",
                ]
            ).stdout.split()
            if names:
                resources = json.loads(
                    self.command(["docker", kind, "inspect", *names]).stdout
                )
                if any(
                    item.get("Labels", {}).get(LABEL) != self.manifest["run_id"]
                    for item in resources
                ):
                    raise RuntimeError("refusing cleanup: foreign project resource")
        self.command(
            self.compose
            + ["--profile", "ingestion", "down", "--volumes", "--timeout", "10"],
            timeout=90,
        )
        if self.owned():
            raise RuntimeError("owned containers remain after cleanup")
        for kind in ("volume", "network"):
            if self.command(
                [
                    "docker",
                    kind,
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={self.manifest['project']}",
                ]
            ).stdout.strip():
                raise RuntimeError(f"owned {kind} remains after cleanup")
        if self.manifest.get("application"):
            tag = self.manifest["project"] + "-collector:local"
            inspected = self.command(["docker", "image", "inspect", tag], check=False)
            if inspected.returncode == 0:
                built = json.loads(inspected.stdout)
                if (
                    len(built) != 1
                    or built[0]["Config"].get("Labels", {}).get(LABEL)
                    != self.manifest["run_id"]
                ):
                    raise RuntimeError("refusing to remove a foreign local test image")
                self.command(["docker", "image", "rm", tag])
        save(
            self.directory / "cleanup-evidence.json",
            {
                "project": self.manifest["project"],
                "containers_volumes_networks_absent": True,
                "test_data_removed": True,
                "evidence_retained": True,
            },
        )
        print(
            f"Removed only disposable project {self.manifest['project']}; evidence retained at {self.directory}",
            flush=True,
        )


def load_run(directory: Path) -> Run:
    directory = directory.resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        directory.name != "property-catalog-managed-" + manifest["run_id"]
        or manifest["project"] != PREFIX + manifest["run_id"]
    ):
        raise ValueError("not a managed smoke directory/project")
    if len(manifest["run_id"]) != 16 or any(
        c not in "0123456789abcdef" for c in manifest["run_id"]
    ):
        raise ValueError("invalid run token")
    raw = (directory / "compose.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["compose_sha256"]:
        raise ValueError("compose file changed since this run was staged")
    return Run(directory, manifest)


def smoke(run: Run, python: str) -> dict:
    m = run.manifest
    print(
        f"Starting only {m['project']}; ports={m['ports']}; evidence={run.directory}",
        flush=True,
    )
    if run.command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={m['project']}",
        ]
    ).stdout.strip():
        raise RuntimeError("new project unexpectedly already exists")
    for kind, suffix in (
        ("volume", "clickhouse-data"),
        ("volume", "postgres-data"),
        ("network", "default"),
    ):
        name = f"{m['project']}_{suffix}"
        if run.command(
            ["docker", kind, "ls", "-q", "--filter", f"name=^{name}$"]
        ).stdout.strip():
            raise RuntimeError(f"refusing reuse of existing {kind} {name}")
    run.command(
        run.compose + ["up", "-d", "--wait", "--wait-timeout", "180"], timeout=420
    )
    run.owned()
    report = {"project": m["project"], "stages": {}}
    report["images"] = {
        item["Config"]["Labels"]["com.docker.compose.service"]: item["Image"]
        for item in json.loads(run.command(["docker", "inspect", *run.owned()]).stdout)
    }
    for name in (
        "002_spans_v2.sql",
        "013_attributes_extra_as_string.sql",
        "014_peerdb_is_deleted_back_compat.sql",
    ):
        run.execute(
            "clickhouse",
            ["clickhouse-client", "--database", "default", "--multiquery"],
            stdin=(
                ROOT / "futureagi/tracer/services/clickhouse/v2/schema" / name
            ).read_text(),
        )
    before = run.execute(
        "clickhouse",
        [
            "clickhouse-client",
            "--query",
            f"SELECT count() FROM system.databases WHERE name='{m['database']}'",
        ],
    ).stdout.strip()
    if before != "0":
        raise RuntimeError("catalog is not a fresh destination")
    for _ in range(2):
        run.execute(
            "clickhouse",
            [
                "env",
                f"PROPERTY_CATALOG_TARGET_DATABASE={m['database']}",
                "PROPERTY_CATALOG_SOURCE_DATABASE=default",
                "PROPERTY_CATALOG_SCHEMA_DIRECTORY=/schema",
                "CLICKHOUSE_HOST=127.0.0.1",
                "sh",
                "/bootstrap/bootstrap_clickhouse.sh",
            ],
        )
        run.execute(
            "postgres",
            [
                "env",
                "PGHOST=127.0.0.1",
                "PGUSER=smoke_admin",
                "PGDATABASE=managed_smoke",
                f"PGPASSWORD={m['password']}",
                "sh",
                "/bootstrap/bootstrap_postgres.sh",
            ],
        )
    report["stages"]["fresh_schema_and_role_bootstrap"] = "passed_twice"
    for topic in (m["candidate_topic"], m["ordered_topic"]):
        run.execute(
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
                "--config",
                "max.message.bytes=1048576",
            ],
            timeout=30,
        )
    # Compile current checkout code before the finite fixture lease starts.
    binary = run.directory / "candidate-smoke"
    run.command(
        ["go", "build", "-o", str(binary), str(HERE / "candidate_smoke.go")],
        cwd=ROOT / "fi-collector",
        timeout=180,
    )
    if m.get("ordered_chain") or m.get("application"):
        from ordered_chain import build

        build(run)
    if m.get("application"):
        from ingestion_smoke import build_collector

        build_collector(run)
    run.command(
        [python, str(HERE / "source_parity.py"), "seed", str(run.directory)],
        timeout=100,
    )
    report["stages"]["installation_identity_sql_and_restart"] = json.loads(
        (run.directory / "identity-evidence.json").read_text()
    )
    save(run.directory / "partial-result.json", report)
    run.command([str(binary), str(run.directory / "candidate-input.json")], timeout=45)
    run.command(
        [python, str(HERE / "source_parity.py"), "verify", str(run.directory)],
        timeout=100,
    )
    report["stages"]["canonical_source_and_managed_candidate_kafka_parity"] = (
        json.loads((run.directory / "parity-evidence.json").read_text())
    )
    if m.get("ordered_chain"):
        run.command(
            [python, str(HERE / "ordered_chain.py"), str(run.directory)], timeout=100
        )
        report["stages"]["guarded_ordered_delivery_and_restart"] = json.loads(
            (run.directory / "ordered-chain-evidence.json").read_text()
        )
    report["stages"]["managed_workspace_lifecycle"] = {
        "status": "blocked",
        "reason": "See lifecycle_contract.md. A real bounded reservation is tested, not qualification/activation.",
        "required": [
            "parent root Compose no-version startup and managed reader activation wiring",
            "backend relational-source fixture and full supervisor/sequencer/consumer activation run",
        ],
    }
    if m.get("application"):
        from application_smoke import execute

        save(run.directory / "partial-result.json", report)
        report["stages"]["managed_workspace_lifecycle"] = execute(run, python)
        from schema_upgrade import execute as upgrade

        report["stages"]["existing_control_schema_upgrade"] = upgrade(run)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "plan", "cleanup"))
    parser.add_argument(
        "--application",
        action="store_true",
        help="Also run normal OSS migrations, ORM fixtures and actual continuous supervisor on a second fresh catalog",
    )
    parser.add_argument(
        "--ordered-chain",
        action="store_true",
        help="Also build/run current Go sequencer and guarded consumer against this new project",
    )
    parser.add_argument(
        "--directory", type=Path, help="Exact retained run directory, cleanup only"
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python with Django and clickhouse-driver installed",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Retain only this run's Docker resources for inspection",
    )
    parser.add_argument(
        "--transport-only",
        action="store_true",
        help="Accept infrastructure/candidate parity as the requested scope; never reports lifecycle success",
    )
    args = parser.parse_args()
    if args.action == "cleanup":
        if not args.directory:
            parser.error("cleanup requires --directory")
        load_run(args.directory).cleanup()
        return 0
    if args.directory:
        parser.error("new runs always allocate a new directory")
    run_id = secrets.token_hex(8)
    directory = Path(tempfile.gettempdir()).resolve() / (
        "property-catalog-managed-" + run_id
    )
    directory.mkdir(mode=0o700)  # exclusive: never reuse an existing directory
    manifest = {
        "run_id": run_id,
        "project": PREFIX + run_id,
        "ports": reserve_ports(),
        "password": secrets.token_hex(24),
        "cluster_id": secrets.token_urlsafe(16),
        "database": "property_catalog_dev_smoke_" + run_id,
        "candidate_topic": f"futureagi.managed.{run_id}.candidates.v2",
        "ordered_topic": f"futureagi.managed.{run_id}.ordered.v1",
        "ordered_chain": args.ordered_chain,
        "application": args.application,
    }
    save(directory / "compose.json", compose_document(manifest))
    (directory / "empty.env").touch(mode=0o600)
    manifest["compose_sha256"] = hashlib.sha256(
        (directory / "compose.json").read_bytes()
    ).hexdigest()
    save(directory / "manifest.json", manifest)
    print(f"Run directory: {directory}", flush=True)
    run = Run(directory, manifest)
    run.command(run.compose + ["config", "--quiet"])
    if args.action == "plan":
        print("Compose validated; no Docker resources created.")
        return 0
    before = run.command(["docker", "ps", "-q"]).stdout.split()
    save(directory / "preexisting-containers.json", before)
    result = {"status": "failed"}
    exit_code = 1
    try:
        result = smoke(run, args.python)
        result["status"] = (
            "managed_integration_passed_release_gate_incomplete"
            if args.application
            else "transport_smoke_passed_lifecycle_blocked"
        )
        exit_code = 0 if args.transport_only else 2
    except (Exception, KeyboardInterrupt) as exc:
        if (directory / "partial-result.json").exists():
            result = json.loads((directory / "partial-result.json").read_text())
            result["status"] = "failed"
        result["error"] = str(exc)
        print(f"Smoke stopped: {exc}", file=sys.stderr, flush=True)
    finally:
        save(directory / "result.json", result)
        run.deadline = time.monotonic() + 150
        try:
            run.command(
                run.compose
                + ["--profile", "ingestion", "logs", "--no-color", "--tail", "100"],
                check=False,
            )
            if not args.keep:
                run.cleanup()
        finally:
            after = run.command(["docker", "ps", "-q"]).stdout.split()
            save(
                directory / "preexisting-containers-after.json",
                {
                    "before": before,
                    "still_running": sorted(set(before) & set(after)),
                    "no_longer_running": sorted(set(before) - set(after)),
                },
            )
    print(f"Result: {directory / 'result.json'}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
