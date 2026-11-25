#!/usr/bin/env python3
"""Backend CI: record which image each test service container actually runs.

    service_receipt.py OUT.json

Run in futureagi after the compose health gate and before any pytest. The
services use floating tags (postgres:16-alpine, redis:7-alpine,
clickhouse/clickhouse-server:25.3-alpine), so the tag alone does not say what
a run tested against. key.sh already hashes the running image IDs into the
migrated-DB key, which is in receipt.json and must agree across shards, but a
hash cannot be read back. This writes them out, next to receipt.json, in the
shard's collected-ids artifact. No verdict reads the file.

Observation only:
- It reads the running containers and the images they run, by the
  container's own image ID. It never names a tag to the daemon, never pulls,
  and never asks a registry.
- It records allowlisted fields only. The Go templates ask the daemon for
  exactly those fields, so nothing else (labels, env, proxies, registries)
  is read.
- A read that fails or times out is null plus a warning. Every call has
  CALL_SECONDS, and all calls together BUDGET_SECONDS, well inside the step's
  timeout. The exit status is always 0.

What `.Image` means depends on the daemon's image store, which the receipt
records (daemon.driver, daemon.driver_status). With the classic store it is
the image's config digest. With the containerd store it is the digest of the
manifest or index the tag resolved to.

Standard library only.
"""

import json
import os
import re
import subprocess
import sys
import time

COMPOSE = ("docker", "compose", "-f", "docker-compose.test.yml", "-p", "futureagi-test")
CALL_SECONDS = 10
BUDGET_SECONDS = 60
TITLE = "Service receipt"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")

# Each template names every field it reads; the test pins these sets.
CONTAINER_FORMAT = (
    '{"id":{{json .Id}},"image":{{json .Image}},'
    '"config_image":{{json .Config.Image}},"started_at":{{json .State.StartedAt}}}'
)
IMAGE_FORMAT = (
    '{"repo_digests":{{json .RepoDigests}},"os":{{json .Os}},'
    '"architecture":{{json .Architecture}},"variant":{{json .Variant}},'
    '"created":{{json .Created}}}'
)
INFO_FORMAT = '{"driver":{{json .Driver}},"driver_status":{{json .DriverStatus}}}'
VERSION_FORMAT = "{{json .Server.Version}}"

# The server's own answer, from inside the running container.
SERVER_COMMANDS = {
    "test-clickhouse": ("clickhouse-client", "-q", "SELECT version()"),
    "test-db": (
        "psql",
        "-U",
        "test_user",
        "-d",
        "test_tfc",
        "-Atc",
        "SELECT version()",
    ),
    "test-redis": ("redis-cli", "INFO", "server"),
}
REDIS_FIELDS = ("redis_version", "redis_build_id", "redis_mode")


def escape(message):
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


class Reader:
    def __init__(self, out):
        self.out = out
        self.deadline = time.monotonic() + BUDGET_SECONDS

    def warn(self, message):
        self.out.write(f"::warning title={TITLE}::{escape(message)}\n")

    def run(self, argv, what):
        """The command's stdout, or None after a warning."""
        left = self.deadline - time.monotonic()
        if left <= 0:
            self.warn(f"{what}: not read, the {BUDGET_SECONDS:g} s budget is spent")
            return None
        timeout = min(CALL_SECONDS, left)
        try:
            proc = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self.warn(f"{what}: no answer within {round(timeout, 1):g} s")
            return None
        except OSError as exc:
            self.warn(f"{what}: {exc}")
            return None
        if proc.returncode != 0:
            first = (proc.stderr.strip().splitlines() or [""])[0][:200]
            self.warn(f"{what}: exit {proc.returncode} {first}".rstrip())
            return None
        return proc.stdout

    def json(self, argv, what):
        text = self.run(argv, what)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            self.warn(f"{what}: not JSON")
            return None


def pick(document, keys):
    if not isinstance(document, dict):
        return None
    return {key: document.get(key) for key in keys}


def server(reader, service):
    command = SERVER_COMMANDS.get(service)
    if command is None:
        return None
    text = reader.run(
        (*COMPOSE, "exec", "-T", service, *command), f"{service} server version"
    )
    if text is None:
        return None
    if service != "test-redis":
        return {"version": text.strip()}
    fields = {}
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        if key in REDIS_FIELDS:
            fields[key] = value
    if not fields:
        reader.warn(f"{service} server version: no {REDIS_FIELDS[0]} in INFO server")
        return None
    return {key: fields.get(key) for key in REDIS_FIELDS}


def service_entry(reader, service):
    entry = {"container": None, "image": None, "server": None}
    listed = reader.run((*COMPOSE, "ps", "-q", service), f"{service} container")
    if listed is None:
        return entry
    ids = listed.split()
    if len(ids) != 1:
        reader.warn(f"{service} container: {len(ids)} running, expected 1")
        return entry
    entry["container"] = pick(
        reader.json(
            (
                "docker",
                "inspect",
                "--type",
                "container",
                "--format",
                CONTAINER_FORMAT,
                ids[0],
            ),
            f"{service} container",
        ),
        ("id", "image", "config_image", "started_at"),
    )
    image_id = (entry["container"] or {}).get("image")
    if isinstance(image_id, str) and IMAGE_ID.fullmatch(image_id):
        entry["image"] = pick(
            reader.json(
                ("docker", "image", "inspect", "--format", IMAGE_FORMAT, image_id),
                f"{service} image",
            ),
            ("repo_digests", "os", "architecture", "variant", "created"),
        )
    elif entry["container"] is not None:
        reader.warn(f"{service} image: the container names no sha256 image ID")
    entry["server"] = server(reader, service)
    return entry


def collect(reader):
    info = pick(
        reader.json(("docker", "info", "--format", INFO_FORMAT), "docker info"),
        ("driver", "driver_status"),
    ) or {"driver": None, "driver_status": None}
    version = reader.json(
        ("docker", "version", "--format", VERSION_FORMAT), "docker version"
    )
    receipt = {
        "schema": 1,
        "daemon": {
            "server_version": version if isinstance(version, str) else None,
            **info,
        },
        "services": {},
    }
    names = reader.run((*COMPOSE, "config", "--services"), "compose services")
    for service in sorted((names or "").split()):
        receipt["services"][service] = service_entry(reader, service)
    return receipt


def summary_line(service, entry):
    container = entry["container"] or {}
    image = entry["image"] or {}
    platform = "/".join(
        part
        for part in (image.get("os"), image.get("architecture"), image.get("variant"))
        if part
    )
    found = entry["server"] or {}
    version = found.get("version") or found.get("redis_version") or "unknown"
    return (
        f"- `{service}`: {container.get('config_image') or 'unknown'} ran "
        f"{container.get('image') or 'unknown'} ({platform or 'unknown platform'}); server {version}\n"
    )


def main(argv=None, out=sys.stdout):
    args = sys.argv[1:] if argv is None else argv
    reader = Reader(out)
    try:
        if len(args) != 1:
            reader.warn("usage: service_receipt.py OUT.json")
            return 0
        receipt = collect(reader)
        text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        os.makedirs(os.path.dirname(os.path.abspath(args[0])), exist_ok=True)
        with open(args[0], "w", encoding="utf-8") as handle:
            handle.write(text)
        out.write(text)
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("Service containers this shard ran:\n")
                for service, entry in receipt["services"].items():
                    handle.write(summary_line(service, entry))
    except Exception as exc:  # noqa: BLE001 - observation only; it must never fail the shard
        reader.warn(f"could not write the receipt: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
