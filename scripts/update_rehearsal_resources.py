#!/usr/bin/env python3
"""Native Linux resource envelope for the real normal Standalone bootstrap.

This producer is deliberately limited to ephemeral native amd64 GitHub Actions
runners. It does not start containers, alter Compose, run migrations, or mount a
helper onto enrolled stores. The caller brackets the ordinary
AutomaticRuntime.start_and_wait call after its exact target containers have been
created and before any target container is started.

The public measurement has no invented throughput or per-migration allocation.
Private evidence binds raw samples to the cold snapshot generation, signed target
digest, exact container/volume/storage identities, ordered pending plan, explicit
per-migration registered A9 table scope, and independently measured reference
tables. Samples are bounded observations; they do not bound unobserved spikes
between samples.

Counter contract (all byte counters are bytes):
* disk_bytes: sum of allocated st_blocks * 512 under each exact enrolled named
  volume root, each exact target container overlay2 UpperDir, and each exact
  local log path plus numeric rotations. json-file's fixed sink is bound before
  first start and must match Docker's reported path once running. Hardlinks are counted
  once. Unrelated filesystem activity cannot offset owned growth. Symlinks,
  special files, filesystem-device crossings, omitted roots, or unreadable roots refuse.
* wal_bytes: allocated st_blocks * 512 for regular files directly in the exact
  Postgres volume's pg_wal directory; no WAL contents are read.
* temp_bytes: allocated st_blocks * 512 for Postgres base/<db>/pgsql_tmp and
  ClickHouse volume tmp files; no file contents are read.
* memory_bytes: sum of current memory usage for the three exact target container
  cgroups (memory.current on cgroup v2 or memory.usage_in_bytes on v1). This is
  cgroup-charged memory, not RSS. A container confirmed by Docker as `created`
  with PID 0 contributes zero because it has no task; missing cgroup reads for a
  running container are errors, never replaced with zero.

A fixed, read-only native host reader is run as the current user. Only if that
reader reports a permissions failure may this module retry it with the fixed
`sudo -n python -I <this-file> --native-reader` entry point, and only on the
native amd64 GitHub runner. The helper accepts bounded JSON data, not commands;
volume paths are re-derived from exact inspected volume names, while writable
layer and local-log paths are validated beneath the fixed /var/lib/docker layout
against exact inspected container identities. Every helper process has a hard
timeout and is reaped as a process group.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

# -I deliberately ignores the working directory and PYTHONPATH. The privileged
# reader imports only this checked-in producer, whose parent is the trusted tree.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update.contracts import pending_migration_plan_hash
from deploy.update.docker_host import bounded_installation
from deploy.update.manifest import canonical_json, parse_image_ref

MEASUREMENT_BASIS = "normal-bootstrap-envelope-v1"
SERVICES = ("app", "postgres", "clickhouse")
VOLUME_ROLES = ("app-data", "postgres-data", "clickhouse-data")
ROLE_SERVICE = {"app-data": "app", "postgres-data": "postgres", "clickhouse-data": "clickhouse"}
MAX_SAMPLE_GAP_SECONDS = 2.0
DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.5
MAX_ENVELOPE_SECONDS = 7200.0
MAX_READ_SECONDS = 2.0
MAX_READER_SECONDS = 1.5
MAX_SAMPLE_COUNT = 20000
MAX_SCAN_ENTRIES = 250000
MAX_COUNTER = 9007199254740991
MAX_READER_INPUT = 1024 * 1024
MAX_READER_OUTPUT = 1024 * 1024
MAX_PRIVATE_EVIDENCE_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}\Z")
TABLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\.[a-z_][a-z0-9_]{0,62}\Z")
APP_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
MIGRATION_NAME = re.compile(r"[A-Za-z0-9_]{1,128}\Z")


class ResourceObservationError(RuntimeError):
    """Closed, path-free resource-observation refusal."""

    def __init__(self, code: str = "resource_observation_unavailable"):
        allowed = {
            "resource_observation_unavailable", "resource_identity_ambiguous",
            "resource_sampling_gap", "resource_sampling_failed",
            "resource_input_invalid", "native_amd64_runner_required",
            "resource_reader_timeout", "resource_reader_failed",
        }
        self.code = code if code in allowed else "resource_observation_unavailable"
        super().__init__(self.code)


def _need(condition: bool, code: str = "resource_observation_unavailable") -> None:
    if not condition:
        raise ResourceObservationError(code)


def _is_int(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_COUNTER


def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _native_amd64_actions() -> bool:
    return (sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64")
            and os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ARCH") == "X64")


def validate_plan_and_tables(pending_plan: Any, tables: Any, migration_tables: Any
                             ) -> tuple[list[dict], list[dict], list[dict], str, str, str]:
    """Validate the exact plan, registered per-migration scope, and reference union.

    migration_tables is mandatory evidence from the independent pre-start loader
    probe after intersection with the checked-in high-volume registry. It is
    never reconstructed from the aggregate `tables` rows.
    """
    _need(type(pending_plan) is list and len(pending_plan) <= 100000, "resource_input_invalid")
    plan: list[dict] = []
    seen_plan: set[tuple[str, str]] = set()
    for item in pending_plan:
        _need(type(item) is dict and set(item) == {"app_label", "name"}
              and type(item["app_label"]) is str and APP_LABEL.fullmatch(item["app_label"]) is not None
              and type(item["name"]) is str and MIGRATION_NAME.fullmatch(item["name"]) is not None,
              "resource_input_invalid")
        key = (item["app_label"], item["name"])
        _need(key not in seen_plan, "resource_input_invalid")
        seen_plan.add(key)
        plan.append(dict(app_label=key[0], name=key[1]))
    try:
        plan_hash = pending_migration_plan_hash(plan)
    except (TypeError, ValueError, RuntimeError):
        raise ResourceObservationError("resource_input_invalid") from None

    _need(type(tables) is list and len(tables) <= 256, "resource_input_invalid")
    refs: list[dict] = []
    seen_tables: set[tuple[str, str]] = set()
    for item in tables:
        _need(type(item) is dict and set(item) == {"engine", "table", "reference_rows", "reference_bytes"}
              and item["engine"] in ("postgres", "clickhouse") and type(item["table"]) is str
              and TABLE_NAME.fullmatch(item["table"]) is not None
              and _is_int(item["reference_rows"]) and item["reference_rows"] >= 100000
              and _is_int(item["reference_bytes"]) and item["reference_bytes"] > 0,
              "resource_input_invalid")
        key = (item["engine"], item["table"])
        _need(key not in seen_tables, "resource_input_invalid")
        seen_tables.add(key)
        refs.append({"engine": key[0], "table": key[1], "reference_rows": item["reference_rows"],
                     "reference_bytes": item["reference_bytes"]})

    _need(type(migration_tables) is list and len(migration_tables) == len(plan), "resource_input_invalid")
    registered_scope: list[dict] = []
    scope_union: set[tuple[str, str]] = set()
    for identity, scope in zip(plan, migration_tables):
        _need(type(scope) is dict and set(scope) == {"app_label", "name", "tables"}
              and scope.get("app_label") == identity["app_label"]
              and scope.get("name") == identity["name"]
              and type(scope.get("tables")) is list and len(scope["tables"]) <= 4096,
              "resource_input_invalid")
        scoped_tables: list[dict] = []
        scoped_seen: set[tuple[str, str]] = set()
        for table in scope["tables"]:
            _need(type(table) is dict and set(table) == {"engine", "table"}
                  and table["engine"] in ("postgres", "clickhouse")
                  and type(table["table"]) is str and TABLE_NAME.fullmatch(table["table"]) is not None,
                  "resource_input_invalid")
            key = (table["engine"], table["table"])
            _need(key not in scoped_seen, "resource_input_invalid")
            scoped_seen.add(key)
            scope_union.add(key)
            scoped_tables.append({"engine": key[0], "table": key[1]})
        registered_scope.append({"app_label": identity["app_label"], "name": identity["name"],
                                 "tables": scoped_tables})

    # The aggregate rows are independently measured registered references. Their
    # exact table set must equal the union of the explicit ordered scope; an
    # omitted scope cannot be inferred from those aggregate rows.
    _need(bool(plan) or (not refs and not registered_scope), "resource_input_invalid")
    _need(scope_union == seen_tables, "resource_input_invalid")
    _need(len(canonical_json(plan)) + len(canonical_json(refs)) + len(canonical_json(registered_scope))
          <= MAX_PRIVATE_EVIDENCE_BYTES, "resource_input_invalid")
    table_hash = _hash(refs)
    scope_hash = _hash(registered_scope)
    return plan, refs, registered_scope, plan_hash, table_hash, scope_hash


def validate_samples(samples: Any, duration_seconds: Any, *, started_monotonic: Any,
                     completed_monotonic: Any) -> None:
    """Reject missing, reset, malformed, or too-sparse raw observations."""
    _need(type(samples) is list and 2 <= len(samples) <= MAX_SAMPLE_COUNT
          and _is_number(duration_seconds) and 0 < duration_seconds <= MAX_ENVELOPE_SECONDS,
          "resource_sampling_failed")
    keys = {"monotonic", "boundary", "disk_bytes", "wal_bytes", "temp_bytes", "memory_bytes"}
    for index, sample in enumerate(samples):
        _need(type(sample) is dict and set(sample) == keys
              and all(_is_int(sample[key]) for key in ("disk_bytes", "wal_bytes", "temp_bytes", "memory_bytes")),
              "resource_sampling_failed")
        stamp = sample["monotonic"]
        _need(_is_number(stamp) and stamp >= 0, "resource_sampling_failed")
        expected = "before_start" if index == 0 else "after_end" if index == len(samples) - 1 else "during"
        _need(sample["boundary"] == expected, "resource_sampling_failed")
        if index:
            delta = stamp - samples[index - 1]["monotonic"]
            _need(0 < delta <= MAX_SAMPLE_GAP_SECONDS, "resource_sampling_gap")
    _need(_is_number(started_monotonic) and _is_number(completed_monotonic)
          and started_monotonic >= 0 and completed_monotonic > started_monotonic
          and math.isclose(completed_monotonic - started_monotonic, duration_seconds,
                           rel_tol=1e-12, abs_tol=1e-9), "resource_sampling_failed")
    _need(samples[0]["monotonic"] <= started_monotonic < completed_monotonic <= samples[-1]["monotonic"]
          and started_monotonic - samples[0]["monotonic"] <= MAX_SAMPLE_GAP_SECONDS
          and samples[-1]["monotonic"] - completed_monotonic <= MAX_SAMPLE_GAP_SECONDS,
          "resource_sampling_gap")


def reduce_envelope(pending_plan: list[dict], tables: list[dict], migration_tables: list[dict],
                    samples: list[dict], duration_seconds: float, *, started_monotonic: float,
                    completed_monotonic: float) -> dict:
    """Produce only the agreed discriminated envelope; never emit a fake rate."""
    validate_samples(samples, duration_seconds, started_monotonic=started_monotonic,
                     completed_monotonic=completed_monotonic)
    # Re-run shape/hash validation here so unit or caller data cannot bypass the
    # same identities used when the live sampler was created.
    plan, refs, scope, _, _, _ = validate_plan_and_tables(pending_plan, tables, migration_tables)
    return {
        "measurement_basis": MEASUREMENT_BASIS,
        "pending_plan": plan,
        "migration_tables": scope,
        "tables": refs,
        "duration_seconds": duration_seconds,
        "peak_disk_growth_bytes": max(0, max(row["disk_bytes"] for row in samples) - samples[0]["disk_bytes"]),
        # These are observed physical peaks, not cumulative WAL production or
        # inferred PostgreSQL throughput. disk_bytes is attributable allocation
        # in exact target-owned roots, not whole-filesystem usage.
        "wal_bytes": max(row["wal_bytes"] for row in samples),
        "temp_bytes": max(row["temp_bytes"] for row in samples),
        "peak_memory_bytes": max(row["memory_bytes"] for row in samples),
    }


def counter_sources() -> dict:
    """Stable, non-sensitive provenance labels retained with private raw samples."""
    return {
        "monotonic": {"unit": "seconds", "source": "time.monotonic at the end of each synchronous read"},
        "disk_bytes": {"unit": "bytes", "source": "sum of allocated st_blocks times 512 under the three exact enrolled volume roots, the exact overlay2 upper directories for the three target containers, and their exact local log files including numeric rotations; hardlinks are counted once; link and special-file inodes are counted without opening or following them; unrelated filesystem activity is excluded; filesystem-device crossings and unreadable roots refuse"},
        "wal_bytes": {"unit": "bytes", "source": "sum of st_blocks times 512 for regular files directly in the exact Postgres volume pg_wal directory; file contents are never read"},
        "temp_bytes": {"unit": "bytes", "source": "sum of st_blocks times 512 for Postgres base/pgsql_tmp spill files and shared file sets, base/<database-oid>/pgsql_tmp files, and ClickHouse volume tmp files; file contents are never read"},
        "memory_bytes": {"unit": "bytes", "source": "sum of exact target-container cgroup memory.current (v2) or memory.usage_in_bytes (v1), not RSS; Docker-confirmed created containers with PID 0 have no task and contribute zero; failed reads for running containers refuse"},
        "sampling": {"maximum_gap_seconds": MAX_SAMPLE_GAP_SECONDS,
                     "spikes_between_samples_measured": False},
    }


@dataclass(frozen=True)
class EnvelopeResult:
    """Successful actual normal-start outcome plus public and private evidence."""
    operation_result: Any
    migration_measurement: Mapping[str, Any]
    private_evidence: Mapping[str, Any]


class NormalBootstrapEnvelopeSampler:
    """Bracket one real normal start using fixed native Docker/host readers.

    Args:
        installation: the already-owned installation used by AutomaticRuntime.
        binding: decoded BackupBinding document from the same cold snapshot.
        roster: exact stopped roster returned by AutomaticRuntime.create(target_images).
        target_image: target manifest's standalone {image, digest}.
        snapshot_generation: SHA256(canonical JSON of the sealed snapshot inventory).
        pending_plan/tables/migration_tables: real pre-start MigrationLoader plan,
            aggregate independently measured A9 references, and mandatory per-plan
            registered reference scope from the independent table-scope probe.

    Call measure(lambda: runtime.start_and_wait(roster, policy, estimate)) once.
    It starts no services itself, changes no image/model, and creates no helpers.
    """

    @property
    def plan(self) -> list[dict]:
        """Return a detached view; mutations cannot alter hash-bound inputs."""
        return copy.deepcopy(self._bound_plan)

    @property
    def tables(self) -> list[dict]:
        return copy.deepcopy(self._bound_tables)

    @property
    def migration_tables(self) -> list[dict]:
        return copy.deepcopy(self._bound_migration_tables)

    @property
    def plan_hash(self) -> str:
        return self._bound_plan_hash

    @property
    def tables_hash(self) -> str:
        return self._bound_tables_hash

    @property
    def migration_tables_hash(self) -> str:
        return self._bound_migration_tables_hash

    def __init__(self, installation: Any, *, binding: Mapping[str, Any], roster: Mapping[str, Any],
                 target_image: Mapping[str, Any], snapshot_generation: str,
                 pending_plan: list[dict], tables: list[dict], migration_tables: list[dict],
                 interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        _need(_native_amd64_actions(), "native_amd64_runner_required")
        _need(_is_number(interval_seconds) and 0 < interval_seconds <= 1.0, "resource_input_invalid")
        _need(callable(clock), "resource_input_invalid")
        _need(type(binding) is dict and type(roster) is dict and type(target_image) is dict,
              "resource_input_invalid")
        _need(HEX64.fullmatch(snapshot_generation) is not None, "resource_input_invalid")
        (self._bound_plan, self._bound_tables, self._bound_migration_tables,
         self._bound_plan_hash, self._bound_tables_hash,
         self._bound_migration_tables_hash) = validate_plan_and_tables(pending_plan, tables, migration_tables)
        self.snapshot_generation = snapshot_generation
        self.clock, self.interval = clock, float(interval_seconds)
        self._used = False
        self._samples: list[dict] = []
        self._sample_lock = threading.Lock()
        self._sample_error: Optional[BaseException] = None
        self._stop = threading.Event()
        self._last_pids: dict[str, int] = {}

        try:
            self.context = installation.runner.docker_context
            _need(type(self.context) is str and 1 <= len(self.context) <= 128, "resource_input_invalid")
            self.installation = installation
            self.binding = dict(binding)
            self.project = binding["project"]
            self.daemon_id = binding["daemon"]
            self.expected_root_ids = dict(binding["volume_root_ids"])
            self.expected_volumes = dict(binding["volumes"])
            _need(type(self.project) is str and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", self.project)
                  and type(self.daemon_id) is str and 1 <= len(self.daemon_id) <= 128,
                  "resource_input_invalid")
            _need(set(self.expected_volumes) == set(VOLUME_ROLES)
                  and set(self.expected_root_ids) == set(VOLUME_ROLES)
                  and set(roster) == set(SERVICES), "resource_input_invalid")
            target_image_ref = target_image.get("image")
            _need(type(target_image_ref) is str and 0 < len(target_image_ref) <= 1024,
                  "resource_input_invalid")
            parsed = parse_image_ref(target_image_ref)
            digest = target_image.get("digest")
            _need(type(digest) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None,
                  "resource_input_invalid")
            _need(parsed.digest in (None, digest), "resource_input_invalid")
            self.target_digest = digest
            self.target_repository = parsed.repository
            self.target_image_ref = target_image_ref.split('@', 1)[0] + '@' + digest
            self.expected_container_ids = {}
            self.expected_image_ids = {}
            self.expected_created = {}
            for service in SERVICES:
                identity = roster[service]
                _need(type(identity) is dict and CONTAINER_ID.fullmatch(identity.get("Id", "")) is not None
                      and re.fullmatch(r"sha256:[0-9a-f]{64}", identity.get("Image", "")) is not None
                      and type(identity.get("Created")) is str and bool(identity["Created"]),
                      "resource_input_invalid")
                self.expected_container_ids[service] = identity["Id"]
                self.expected_image_ids[service] = identity["Image"]
                self.expected_created[service] = identity["Created"]
            self.app_image_id = self.expected_image_ids["app"]
            _need(type(binding.get("images")) is dict
                  and self.expected_image_ids["postgres"] == binding["images"].get("postgres")
                  and self.expected_image_ids["clickhouse"] == binding["images"].get("clickhouse"),
                  "resource_identity_ambiguous")
            self._check_binding_volumes()
            self._initial_identity = self._verify_identity(check_ready=False, check_daemon=True, require_created=True)
            self._identity_public = self._public_identity(self._initial_identity)
            self._identity_sha256 = _hash(self._identity_public)
        except ResourceObservationError:
            raise
        except Exception:
            # Never surface Docker output, host paths, env values, or inspector
            # exceptions through a report artifact.
            raise ResourceObservationError("resource_identity_ambiguous") from None

    def _check_binding_volumes(self) -> None:
        for role in VOLUME_ROLES:
            expected = self.expected_volumes[role]
            _need(type(expected) is dict and expected.get("Name") == self.project + "_" + role
                  and expected.get("Driver") == "local" and expected.get("Scope") == "local"
                  and expected.get("Options") in (None, {}) and bool(expected.get("CreatedAt"))
                  and expected.get("Labels", {}).get("com.docker.compose.project") == self.project
                  and expected["Labels"].get("com.docker.compose.volume") == role
                  and type(expected.get("Mountpoint")) is str,
                  "resource_identity_ambiguous")
            root_id = self.expected_root_ids[role]
            _need(type(root_id) is str and re.fullmatch(r"[0-9]+:[0-9]+", root_id) is not None,
                  "resource_identity_ambiguous")

    def _docker_json(self, *args: str) -> Any:
        try:
            from deploy.update.docker_host import bounded_installation
            inst = bounded_installation(self.installation, MAX_READ_SECONDS, inherit_deadline=True)
            inst.runner.max_output = MAX_READER_OUTPUT
            result = inst.docker_run(*args, check=False)
            _need(result.returncode == 0 and len(result.stdout) + len(result.stderr) <= MAX_READER_OUTPUT,
                  "resource_observation_unavailable")
            return json.loads(result.stdout)
        except ResourceObservationError:
            raise
        except Exception:
            raise ResourceObservationError("resource_observation_unavailable") from None

    def _daemon_info(self) -> tuple[str, str, str]:
        try:
            context_rows = self._docker_json("context", "inspect", self.context)
            _need(type(context_rows) is list and len(context_rows) == 1
                  and context_rows[0].get("Name") == self.context,
                  "resource_identity_ambiguous")
            host = context_rows[0].get("Endpoints", {}).get("docker", {}).get("Host")
            _need(type(host) is str and host.startswith("unix:///"), "resource_identity_ambiguous")
            info_rows = self._docker_json("info", "--format", "{{json .}}")
            _need(type(info_rows) is dict and info_rows.get("ID") == self.daemon_id
                  and info_rows.get("OSType") == "linux"
                  and info_rows.get("Architecture", "").lower() in ("x86_64", "amd64"),
                  "resource_identity_ambiguous")
            docker_root = info_rows.get("DockerRootDir")
            _need(docker_root == "/var/lib/docker", "resource_identity_ambiguous")
            return self.daemon_id, docker_root, host
        except ResourceObservationError:
            raise
        except Exception:
            raise ResourceObservationError("resource_identity_ambiguous") from None

    def _verify_identity(self, *, check_ready: bool, check_daemon: bool, require_created: bool = False) -> dict:
        if check_daemon:
            daemon, docker_root, context_host = self._daemon_info()
        else:
            daemon, docker_root, context_host = self.daemon_id, "/var/lib/docker", "unix://[bound]"
        container_rows = self._docker_json("inspect", *[self.expected_container_ids[s] for s in SERVICES])
        _need(type(container_rows) is list and len(container_rows) == len(SERVICES), "resource_identity_ambiguous")
        by_id = {row.get("Id"): row for row in container_rows if type(row) is dict}
        _need(set(by_id) == set(self.expected_container_ids.values()), "resource_identity_ambiguous")
        volume_names = [self.expected_volumes[role]["Name"] for role in VOLUME_ROLES]
        volume_rows = self._docker_json("volume", "inspect", *volume_names)
        _need(type(volume_rows) is list and len(volume_rows) == len(VOLUME_ROLES), "resource_identity_ambiguous")
        volumes_by_name = {row.get("Name"): row for row in volume_rows if type(row) is dict}
        _need(set(volumes_by_name) == set(volume_names), "resource_identity_ambiguous")

        resolved_volumes: dict[str, dict] = {}
        for role in VOLUME_ROLES:
            expected = self.expected_volumes[role]
            row = volumes_by_name[expected["Name"]]
            actual = {key: row.get(key) for key in
                      ("Name", "Driver", "Mountpoint", "CreatedAt", "Labels", "Options", "Scope")}
            expected_projection = {key: expected.get(key) for key in
                                   ("Name", "Driver", "Mountpoint", "CreatedAt", "Labels", "Options", "Scope")}
            _need(actual == expected_projection and actual["Driver"] == "local"
                  and actual["Scope"] == "local" and actual["Options"] in (None, {})
                  and actual["Labels"].get("com.docker.compose.project") == self.project
                  and actual["Labels"].get("com.docker.compose.volume") == role,
                  "resource_identity_ambiguous")
            mountpoint = actual["Mountpoint"]
            _need(type(mountpoint) is str and mountpoint ==
                  "/var/lib/docker/volumes/" + actual["Name"] + "/_data",
                  "resource_identity_ambiguous")
            resolved_volumes[role] = actual

        container_identity = {}
        current_pids = {}
        for service in SERVICES:
            row = by_id[self.expected_container_ids[service]]
            labels = (row.get("Config") or {}).get("Labels") or {}
            state = row.get("State") or {}
            restart_count = row.get("RestartCount")
            _need(row.get("Id") == self.expected_container_ids[service]
                  and row.get("Image") == self.expected_image_ids[service]
                  and labels.get("com.docker.compose.project") == self.project
                  and labels.get("com.docker.compose.service") == service
                  and row.get("Created") == self.expected_created[service]
                  and type(restart_count) is int and restart_count == 0
                  and row.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") in ("no", "unless-stopped"),
                  "resource_identity_ambiguous")
            if service == "app":
                config_image = (row.get("Config") or {}).get("Image")
                # Normal Compose selects the release tag. Its resolved image ID
                # must match the shipping roster above and the target RepoDigest
                # below; a tag string alone is never identity evidence.
                _need(type(config_image) is str, "resource_identity_ambiguous")
                configured, target = parse_image_ref(config_image), parse_image_ref(self.target_image_ref)
                _need(configured.name == target.name and configured.tag == target.tag
                      and configured.digest in (None, self.target_digest),
                      "resource_identity_ambiguous")
            graph = row.get("GraphDriver")
            graph_data = graph.get("Data") if type(graph) is dict else None
            upper_dir = graph_data.get("UpperDir") if type(graph_data) is dict else None
            _need(type(graph) is dict and graph.get("Name") == "overlay2"
                  and type(upper_dir) is str
                  and re.fullmatch(r"/var/lib/docker/overlay2/[a-f0-9]{64}/diff", upper_dir) is not None,
                  "resource_identity_ambiguous")
            log_config = row.get("HostConfig", {}).get("LogConfig", {})
            log_driver = log_config.get("Type") if type(log_config) is dict else None
            log_path = row.get("LogPath")
            _need(type(log_driver) is str and type(log_path) is str, "resource_identity_ambiguous")
            if log_driver in ("json-file", "local"):
                prefix = "/var/lib/docker/containers/" + self.expected_container_ids[service] + "/"
                if log_driver == 'json-file':
                    expected_log = prefix + self.expected_container_ids[service] + '-json.log'
                    if (not log_path and state.get('Status') == 'created'
                            and type(state.get('Pid')) is int and state['Pid'] == 0):
                        # Docker initializes LogPath in StartLogger, not create.
                        # Bind its fixed json-file sink before startup; subsequent
                        # running inspections must report this exact same path.
                        log_path = expected_log
                    _need(log_path == expected_log, 'resource_identity_ambiguous')
                _need(log_path.startswith(prefix) and log_path != prefix
                      and os.path.normpath(log_path) == log_path
                      and all(part not in ("", ".", "..") for part in log_path[len(prefix):].split("/")),
                      "resource_identity_ambiguous")
            else:
                # Remote/journald drivers may buffer on an unobserved host
                # filesystem. Only explicitly disabled logging has a known zero.
                _need(log_driver == 'none' and not log_path, "resource_identity_ambiguous")
            expected_role = {"app": "app-data", "postgres": "postgres-data", "clickhouse": "clickhouse-data"}[service]
            expected_volume = resolved_volumes[expected_role]
            mounts = row.get("Mounts")
            _need(type(mounts) is list, "resource_identity_ambiguous")
            named = [mount for mount in mounts if mount.get("Type") == "volume"]
            _need(len(named) == 1 and named[0].get("Name") == expected_volume["Name"]
                  and named[0].get("Source") == expected_volume["Mountpoint"]
                  and named[0].get("Destination") == {
                      "app": "/data", "postgres": "/var/lib/postgresql/data",
                      "clickhouse": "/var/lib/clickhouse"}[service]
                  and named[0].get("RW") is True,
                  "resource_identity_ambiguous")
            status, pid = state.get("Status"), state.get("Pid")
            _need(type(pid) is int and pid >= 0, "resource_identity_ambiguous")
            if status == "created":
                _need(pid == 0, "resource_identity_ambiguous")
            else:
                _need(status == "running" and pid > 0 and not state.get("Paused")
                      and not state.get("Restarting") and not state.get("Dead") and not state.get("OOMKilled"),
                      "resource_identity_ambiguous")
                previous = self._last_pids.get(service)
                _need(previous in (None, pid), "resource_identity_ambiguous")
                self._last_pids[service] = pid
            if require_created:
                _need(status == "created" and pid == 0, "resource_identity_ambiguous")
            current_pids[service] = pid
            if check_ready:
                _need(status == "running", "resource_identity_ambiguous")
            container_identity[service] = {
                "id": row["Id"], "image_id": row["Image"], "created": row["Created"],
                "restart_count": restart_count, "upper_dir": upper_dir,
                "log_driver": log_driver, "log_path": log_path,
            }

        _need(self.app_image_id == container_identity["app"]["image_id"], "resource_identity_ambiguous")
        if check_daemon:
            image_rows = self._docker_json("image", "inspect", self.app_image_id)
            _need(type(image_rows) is list and len(image_rows) == 1
                  and image_rows[0].get("Id") == self.app_image_id,
                  "resource_identity_ambiguous")
            repo_digests = image_rows[0].get("RepoDigests") or []
            verified_digest = False
            for reference in repo_digests:
                try:
                    parsed_reference = parse_image_ref(reference)
                except (TypeError, ValueError):
                    continue
                if (parsed_reference.name == parse_image_ref(self.target_image_ref).name
                        and parsed_reference.digest == self.target_digest):
                    verified_digest = True
                    break
            _need(verified_digest, "resource_identity_ambiguous")
        initial = getattr(self, "_initial_identity", None)
        if initial is not None:
            for service in SERVICES:
                _need(all(container_identity[service][key] == initial["containers"][service][key]
                          for key in ("upper_dir", "log_driver", "log_path")),
                      "resource_identity_ambiguous")
        return {
            "daemon_id": daemon,
            "docker_root": docker_root,
            "context_host": context_host,
            "containers": container_identity,
            "volumes": resolved_volumes,
            "pids": current_pids,
        }

    def _public_identity(self, identity: Mapping[str, Any]) -> dict:
        return {
            "daemon_id": identity["daemon_id"],
            "project": self.project,
            "target_repository": self.target_repository,
            "target_digest": self.target_digest,
            "target_app_image_id": self.app_image_id,
            "containers": {service: {
                "id": identity["containers"][service]["id"],
                "image_id": identity["containers"][service]["image_id"],
                "created": identity["containers"][service]["created"],
                "restart_count": identity["containers"][service]["restart_count"],
                "storage_footprint_sha256": _hash({key: identity["containers"][service][key]
                    for key in ("upper_dir", "log_driver", "log_path")}),
            } for service in SERVICES},
            "volumes": {role: {
                "name": identity["volumes"][role]["Name"],
                "created_at": identity["volumes"][role]["CreatedAt"],
                "root_identity": self.expected_root_ids[role],
            } for role in VOLUME_ROLES},
        }

    def _reader_request(self, identity: Mapping[str, Any]) -> dict:
        return {
            "schema_version": 2,
            "docker_root": identity["docker_root"],
            "volumes": [{
                "role": role,
                "name": identity["volumes"][role]["Name"],
                "mountpoint": identity["volumes"][role]["Mountpoint"],
                "root_identity": self.expected_root_ids[role],
            } for role in VOLUME_ROLES],
            "containers": [{
                "service": service,
                "id": identity["containers"][service]["id"],
                "pid": identity["pids"][service],
                "status": "created" if identity["pids"][service] == 0 else "running",
                "upper_dir": identity["containers"][service]["upper_dir"],
                "log_driver": identity["containers"][service]["log_driver"],
                "log_path": identity["containers"][service]["log_path"],
            } for service in SERVICES],
        }

    def _fixed_reader(self, request: dict) -> dict:
        script = str(Path(__file__).resolve())
        payload = canonical_json(request)
        _need(len(payload) <= MAX_READER_INPUT, "resource_reader_failed")
        clean_env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                     "LANG": "C", "LC_ALL": "C"}

        def invoke(argv: list[str]) -> subprocess.CompletedProcess:
            proc = None

            def terminate_group() -> None:
                if proc is None:
                    return
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.communicate(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        raise ResourceObservationError("resource_reader_timeout") from None

            try:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env=clean_env, start_new_session=True)
                try:
                    out, err = proc.communicate(payload, timeout=MAX_READER_SECONDS)
                except subprocess.TimeoutExpired:
                    terminate_group()
                    raise ResourceObservationError("resource_reader_timeout") from None
                _need(len(out) + len(err) <= MAX_READER_OUTPUT, "resource_reader_failed")
                return subprocess.CompletedProcess(argv, proc.returncode, out, err)
            except ResourceObservationError:
                if proc is not None and proc.poll() is None:
                    terminate_group()
                raise
            except Exception:
                if proc is not None and proc.poll() is None:
                    try:
                        terminate_group()
                    except ResourceObservationError:
                        raise
                raise ResourceObservationError("resource_reader_failed") from None
            finally:
                if proc is not None:
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        if pipe is not None:
                            pipe.close()

        command = [sys.executable, "-I", script, "--native-reader"]
        result = invoke(command)
        if result.returncode == 3:
            # This branch cannot be reached on a local host: constructor has
            # already required native amd64 GitHub Actions and no local Docker.
            sudo = "/usr/bin/sudo"
            _need(Path(sudo).is_file() and os.access(sudo, os.X_OK), "resource_reader_failed")
            result = invoke([sudo, "-n", *command])
        _need(result.returncode == 0 and not result.stderr, "resource_reader_failed")
        try:
            counters = _strict_json(result.stdout)
        except Exception:
            raise ResourceObservationError("resource_reader_failed") from None
        _need(type(counters) is dict and set(counters) == {"disk_bytes", "wal_bytes", "temp_bytes", "memory_bytes"}
              and all(_is_int(value) for value in counters.values()), "resource_reader_failed")
        return counters

    def _read(self) -> dict:
        identity = self._verify_identity(check_ready=False, check_daemon=False)
        request = self._reader_request(identity)
        counters = self._fixed_reader(request)
        return dict(counters, monotonic=self.clock())

    def _take_sample(self, boundary: str) -> None:
        _need(boundary in ("before_start", "during", "after_end"), "resource_sampling_failed")
        row = self._read()
        sample = dict(row, boundary=boundary)
        with self._sample_lock:
            _need(len(self._samples) < MAX_SAMPLE_COUNT, "resource_sampling_failed")
            self._samples.append(sample)

    def _sample_loop(self) -> None:
        try:
            while not self._stop.wait(self.interval):
                self._take_sample("during")
        except BaseException as exc:
            self._sample_error = exc
            self._stop.set()

    def measure(self, operation: Callable[[], Any]) -> EnvelopeResult:
        """Run exactly one caller-supplied normal startup between live samples."""
        _need(callable(operation) and not self._used, "resource_input_invalid")
        self._used = True
        worker: Optional[threading.Thread] = None
        operation_result: Any = None
        operation_error: Optional[BaseException] = None
        started = completed = None
        try:
            self._take_sample("before_start")
            started = self.clock()
            worker = threading.Thread(target=self._sample_loop, name="normal-bootstrap-resource-reader", daemon=True)
            worker.start()
            try:
                operation_result = operation()
            except BaseException as exc:
                operation_error = exc
            completed = self.clock()
        finally:
            self._stop.set()
            if worker is not None:
                worker.join(timeout=MAX_READ_SECONDS * 5)
                _need(not worker.is_alive(), "resource_reader_timeout")
        if operation_error is not None:
            raise operation_error
        if self._sample_error is not None:
            raise ResourceObservationError("resource_sampling_failed") from None
        _need(started is not None and completed is not None, "resource_sampling_failed")
        try:
            self._take_sample("after_end")
            self._verify_identity(check_ready=True, check_daemon=True)
        except ResourceObservationError:
            raise
        except Exception:
            raise ResourceObservationError("resource_identity_ambiguous") from None
        duration = completed - started
        samples = [dict(row) for row in self._samples]
        measurement = reduce_envelope(self._bound_plan, self._bound_tables, self._bound_migration_tables,
                                      samples, duration, started_monotonic=started,
                                      completed_monotonic=completed)
        _need(pending_migration_plan_hash(measurement["pending_plan"]) == self._bound_plan_hash
              and _hash(measurement["tables"]) == self._bound_tables_hash
              and _hash(measurement["migration_tables"]) == self._bound_migration_tables_hash,
              "resource_input_invalid")
        raw = {
            "schema_version": 1,
            "measurement_basis": MEASUREMENT_BASIS,
            "snapshot_generation": self.snapshot_generation,
            "target_digest": self.target_digest,
            "target_identity": self._identity_public,
            "target_identity_sha256": self._identity_sha256,
            "ordered_plan_sha256": self._bound_plan_hash,
            "table_references_sha256": self._bound_tables_hash,
            "migration_table_scope_sha256": self._bound_migration_tables_hash,
            "migration_tables": copy.deepcopy(self._bound_migration_tables),
            "counter_sources": counter_sources(),
            "started_monotonic": started,
            "completed_monotonic": completed,
            "measurement": copy.deepcopy(measurement),
            "samples": samples,
        }
        _need(len(canonical_json(raw)) <= MAX_PRIVATE_EVIDENCE_BYTES, "resource_sampling_failed")
        return EnvelopeResult(operation_result, measurement, raw)


def _strict_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))


def _safe_absolute(path: Any) -> str:
    _need(type(path) is str and path.startswith("/") and "\x00" not in path
          and os.path.normpath(path) == path and ".." not in path.split("/"), "resource_reader_failed")
    return path


def _open_dir(path: str) -> int:
    """Open an absolute directory without following any component symlink."""
    path = _safe_absolute(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current = os.open("/", flags)
    try:
        for component in (part for part in path.split("/") if part):
            child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
        result, current = current, -1
        return result
    finally:
        if current >= 0:
            os.close(current)


def _root_id(fd: int) -> str:
    info = os.fstat(fd)
    return "%d:%d" % (info.st_dev, info.st_ino)


def _allocated_files(directory_fd: int, *, recurse: bool, counter: list[int]) -> int:
    total = 0
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            counter[0] += 1
            _need(counter[0] <= MAX_SCAN_ENTRIES, "resource_reader_failed")
            before = entry.stat(follow_symlinks=False)
            _need(before.st_dev == os.fstat(directory_fd).st_dev and not stat_is_symlink(before.st_mode),
                  "resource_reader_failed")
            if is_regular(before.st_mode):
                blocks = getattr(before, "st_blocks", None)
                _need(type(blocks) is int and blocks >= 0, "resource_reader_failed")
                total += blocks * 512
            elif is_directory(before.st_mode) and recurse:
                child = os.open(entry.name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
                try:
                    total += _allocated_files(child, recurse=True, counter=counter)
                finally:
                    os.close(child)
            elif is_directory(before.st_mode):
                # PostgreSQL pg_wal also contains archive_status; it is not WAL
                # segment allocation and is covered by disk_bytes in any case.
                continue
            else:
                _need(False, "resource_reader_failed")
            after = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
            _need((before.st_dev, before.st_ino, before.st_mode) == (after.st_dev, after.st_ino, after.st_mode),
                  "resource_reader_failed")
    _need(0 <= total <= MAX_COUNTER, "resource_reader_failed")
    return total


def _allocated_inode(info: os.stat_result, seen: set[tuple[int, int]]) -> int:
    key = (info.st_dev, info.st_ino)
    if key in seen:
        return 0
    seen.add(key)
    blocks = getattr(info, "st_blocks", None)
    _need(type(blocks) is int and blocks >= 0, "resource_reader_failed")
    value = blocks * 512
    _need(value <= MAX_COUNTER, "resource_reader_failed")
    return value


def _owned_allocated_tree(directory_fd: int, counter: list[int],
                          seen: set[tuple[int, int]], *, root_device: Optional[int] = None) -> int:
    """Count allocated bytes below one exact owned directory, without statvfs."""
    root_info = os.fstat(directory_fd)
    _need(stat.S_ISDIR(root_info.st_mode), "resource_reader_failed")
    device = root_info.st_dev if root_device is None else root_device
    _need(root_info.st_dev == device, "resource_reader_failed")
    total = _allocated_inode(root_info, seen)
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            counter[0] += 1
            _need(counter[0] <= MAX_SCAN_ENTRIES, "resource_reader_failed")
            before = entry.stat(follow_symlinks=False)
            _need(before.st_dev == device, "resource_reader_failed")
            if is_directory(before.st_mode):
                child = os.open(entry.name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
                try:
                    opened = os.fstat(child)
                    _need((opened.st_dev, opened.st_ino, opened.st_mode)
                          == (before.st_dev, before.st_ino, before.st_mode), "resource_reader_failed")
                    total += _owned_allocated_tree(child, counter, seen, root_device=device)
                finally:
                    os.close(child)
            else:
                # ClickHouse creates in-volume symlinks, and live container
                # upper layers contain Unix sockets/FIFOs. Count their allocated
                # inodes without opening them or following any link target.
                total += _allocated_inode(before, seen)
            after = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
            _need((before.st_dev, before.st_ino, before.st_mode)
                  == (after.st_dev, after.st_ino, after.st_mode), "resource_reader_failed")
            _need(total <= MAX_COUNTER, "resource_reader_failed")
    _need(0 <= total <= MAX_COUNTER, "resource_reader_failed")
    return total


def _owned_log_bytes(path: str, *, counter: list[int], seen: set[tuple[int, int]]) -> int:
    """Measure one Docker-reported local log leaf and its numeric rotations."""
    parent, leaf = os.path.split(path)
    parent_fd = _open_dir(parent)
    try:
        device = os.fstat(parent_fd).st_dev
        total = 0
        with os.scandir(parent_fd) as entries:
            for entry in entries:
                if entry.name != leaf and re.fullmatch(re.escape(leaf) + r"\.[0-9]+(?:\.gz)?", entry.name) is None:
                    continue
                counter[0] += 1
                _need(counter[0] <= MAX_SCAN_ENTRIES, "resource_reader_failed")
                before = entry.stat(follow_symlinks=False)
                _need(before.st_dev == device and is_regular(before.st_mode), "resource_reader_failed")
                total += _allocated_inode(before, seen)
                after = os.stat(entry.name, dir_fd=parent_fd, follow_symlinks=False)
                _need((before.st_dev, before.st_ino, before.st_mode)
                      == (after.st_dev, after.st_ino, after.st_mode), "resource_reader_failed")
                _need(total <= MAX_COUNTER, "resource_reader_failed")
        return total
    finally:
        os.close(parent_fd)


def stat_is_symlink(mode: int) -> bool:
    return stat.S_ISLNK(mode)


def is_regular(mode: int) -> bool:
    return stat.S_ISREG(mode)


def is_directory(mode: int) -> bool:
    return stat.S_ISDIR(mode)


def _postgres_resource_bytes(root_fd: int, counter: list[int]) -> tuple[int, int]:
    wal_fd = os.open("pg_wal", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                     | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)
    base_fd = os.open("base", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                      | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)
    try:
        wal = _allocated_files(wal_fd, recurse=False, counter=counter)
        temp = 0
        with os.scandir(base_fd) as databases:
            for database in databases:
                counter[0] += 1
                _need(counter[0] <= MAX_SCAN_ENTRIES, "resource_reader_failed")
                if database.name == 'pgsql_tmp':
                    # Ordinary sort/hash spill files use base/pgsql_tmp, not a
                    # database-OID child. Shared file sets can add subdirectories.
                    temp_fd = os.open(database.name, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
                                      | getattr(os, 'O_NOFOLLOW', 0), dir_fd=base_fd)
                    try:
                        temp += _allocated_files(temp_fd, recurse=True, counter=counter)
                    finally:
                        os.close(temp_fd)
                    continue
                _need(re.fullmatch(r"[0-9]+", database.name) is not None, "resource_reader_failed")
                info = database.stat(follow_symlinks=False)
                _need(stat.S_ISDIR(info.st_mode), "resource_reader_failed")
                db_fd = os.open(database.name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0), dir_fd=base_fd)
                try:
                    try:
                        temp_fd = os.open("pgsql_tmp", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                          | getattr(os, "O_NOFOLLOW", 0), dir_fd=db_fd)
                    except FileNotFoundError:
                        # A verified database directory with no pgsql_tmp entry
                        # is a measured empty temporary directory, not a failed read.
                        temp_fd = None
                    if temp_fd is not None:
                        try:
                            temp += _allocated_files(temp_fd, recurse=False, counter=counter)
                        finally:
                            os.close(temp_fd)
                finally:
                    os.close(db_fd)
        # External PostgreSQL tablespaces can put temp files outside the enrolled
        # named volume. Refuse instead of silently undercounting that data.
        try:
            links_fd = os.open("pg_tblspc", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                               | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)
        except FileNotFoundError:
            links_fd = None
        if links_fd is not None:
            try:
                with os.scandir(links_fd) as entries:
                    _need(next(entries, None) is None, "resource_reader_failed")
            finally:
                os.close(links_fd)
        return wal, temp
    finally:
        os.close(wal_fd)
        os.close(base_fd)


def _open_optional_dir(parent_fd: int, name: str) -> Optional[int]:
    try:
        return os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    except FileNotFoundError:
        return None


def _memory_for_container(row: Mapping[str, Any]) -> int:
    """Host helper implementation; path is obtained only from /proc/<exact PID>."""
    pid, cid, status = row["pid"], row["id"], row["status"]
    _need(CONTAINER_ID.fullmatch(cid) is not None, "resource_reader_failed")
    if status == "created" and pid == 0:
        return 0
    _need(status == "running" and type(pid) is int and pid > 0, "resource_reader_failed")

    def start_time() -> str:
        with open("/proc/%d/stat" % pid, "rb") as stream:
            raw = stream.read(8192)
        _need(len(raw) <= 8192 and raw.endswith(b"\n"), "resource_reader_failed")
        close = raw.rfind(b")")
        _need(close > 0, "resource_reader_failed")
        fields = raw[close + 2:].split()
        _need(len(fields) > 19, "resource_reader_failed")
        return fields[19].decode("ascii")

    before = start_time()
    with open("/proc/%d/cgroup" % pid, "rb") as stream:
        raw = stream.read(65537)
    _need(len(raw) <= 65536, "resource_reader_failed")
    rows = raw.decode("ascii", "strict").splitlines()
    v2_path = None
    v1_path = None
    for line in rows:
        parts = line.split(":", 2)
        _need(len(parts) == 3, "resource_reader_failed")
        if parts[0] == "0" and parts[1] == "":
            v2_path = parts[2]
        if "memory" in parts[1].split(","):
            v1_path = parts[2]
    if v2_path is not None:
        cgroup_path, filename, root = v2_path, "memory.current", "/sys/fs/cgroup"
    elif v1_path is not None:
        cgroup_path, filename, root = v1_path, "memory.usage_in_bytes", "/sys/fs/cgroup/memory"
    else:
        _need(False, "resource_reader_failed")
    _need(cgroup_path.startswith("/") and os.path.normpath(cgroup_path) == cgroup_path
          and ".." not in cgroup_path.split("/") and not any("\\" in part for part in cgroup_path.split("/")),
          "resource_reader_failed")
    components = [part for part in cgroup_path.split("/") if part]
    _need(any(part == cid or part == "docker-" + cid + ".scope" for part in components),
          "resource_reader_failed")
    cgroup_fd = _open_dir(os.path.join(root, *components))
    try:
        fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=cgroup_fd)
        try:
            value = os.read(fd, 128)
        finally:
            os.close(fd)
    finally:
        os.close(cgroup_fd)
    _need(value.endswith(b"\n") and value[:-1].isdigit(), "resource_reader_failed")
    after = start_time()
    _need(before == after, "resource_reader_failed")
    result = int(value[:-1])
    _need(0 <= result <= MAX_COUNTER, "resource_reader_failed")
    return result


def _native_reader(request: Any) -> dict:
    """Fixed host-only observation; never runs a caller-supplied command."""
    _need(type(request) is dict and set(request) == {"schema_version", "docker_root", "volumes", "containers"}
          and type(request["schema_version"]) is int and request["schema_version"] == 2,
          "resource_reader_failed")
    docker_root = _safe_absolute(request["docker_root"])
    _need(docker_root == "/var/lib/docker", "resource_reader_failed")
    volumes = request["volumes"]
    _need(type(volumes) is list and len(volumes) == len(VOLUME_ROLES), "resource_reader_failed")
    volume_map = {}
    for row in volumes:
        _need(type(row) is dict and set(row) == {"role", "name", "mountpoint", "root_identity"}
              and row["role"] in VOLUME_ROLES and row["role"] not in volume_map
              and type(row["name"]) is str and VOLUME_NAME.fullmatch(row["name"]) is not None
              and type(row["root_identity"]) is str and re.fullmatch(r"[0-9]+:[0-9]+", row["root_identity"]),
              "resource_reader_failed")
        mountpoint = _safe_absolute(row["mountpoint"])
        _need(mountpoint == "/var/lib/docker/volumes/" + row["name"] + "/_data",
              "resource_reader_failed")
        volume_map[row["role"]] = dict(row, mountpoint=mountpoint)
    _need(set(volume_map) == set(VOLUME_ROLES), "resource_reader_failed")
    containers = request["containers"]
    _need(type(containers) is list and len(containers) == len(SERVICES), "resource_reader_failed")
    container_map = {}
    for row in containers:
        _need(type(row) is dict and set(row) == {
                  "service", "id", "pid", "status", "upper_dir", "log_driver", "log_path"}
              and row["service"] in SERVICES and row["service"] not in container_map
              and CONTAINER_ID.fullmatch(row["id"]) is not None and type(row["pid"]) is int and row["pid"] >= 0
              and row["status"] in ("created", "running")
              and type(row["upper_dir"]) is str
              and re.fullmatch(r"/var/lib/docker/overlay2/[a-f0-9]{64}/diff", row["upper_dir"]) is not None
              and type(row["log_driver"]) is str and type(row["log_path"]) is str,
              "resource_reader_failed")
        if row["log_driver"] in ("json-file", "local"):
            prefix = "/var/lib/docker/containers/" + row["id"] + "/"
            relative = row["log_path"][len(prefix):] if row["log_path"].startswith(prefix) else ""
            _need(bool(relative) and os.path.normpath(row["log_path"]) == row["log_path"]
                  and all(part not in ("", ".", "..") for part in relative.split("/")),
                  "resource_reader_failed")
        else:
            _need(row['log_driver'] == 'none' and not row['log_path'], 'resource_reader_failed')
        container_map[row["service"]] = row
    _need(set(container_map) == set(SERVICES), "resource_reader_failed")

    docker_fd = _open_dir(docker_root)
    volume_fds = {}
    upper_fds = {}
    try:
        for role in VOLUME_ROLES:
            entry = volume_map[role]
            fd = _open_dir(entry["mountpoint"])
            volume_fds[role] = fd
            _need(_root_id(fd) == entry["root_identity"], "resource_reader_failed")
        # Reject overlapping directory roots before counting to avoid duplicate
        # storage and accidental widening beyond each enrolled owner.
        directory_roots = [entry["mountpoint"] for entry in volume_map.values()]
        directory_roots.extend(container_map[service]["upper_dir"] for service in SERVICES)
        for index, path in enumerate(directory_roots):
            _safe_absolute(path)
            for other in directory_roots[index + 1:]:
                _need(path != other and not other.startswith(path.rstrip("/") + "/")
                      and not path.startswith(other.rstrip("/") + "/"), "resource_reader_failed")
        for service in SERVICES:
            path = container_map[service]["upper_dir"]
            upper_fds[service] = _open_dir(path)
        counter = [0]
        seen_inodes: set[tuple[int, int]] = set()
        disk = 0
        for role in VOLUME_ROLES:
            disk += _owned_allocated_tree(volume_fds[role], counter, seen_inodes)
        for service in SERVICES:
            disk += _owned_allocated_tree(upper_fds[service], counter, seen_inodes)
        for service in SERVICES:
            container = container_map[service]
            if container["log_path"]:
                disk += _owned_log_bytes(container["log_path"], counter=counter, seen=seen_inodes)
        _need(0 <= disk <= MAX_COUNTER, "resource_reader_failed")
        postgres_fd = volume_fds["postgres-data"]
        clickhouse_fd = volume_fds["clickhouse-data"]
        wal_bytes, postgres_temp = _postgres_resource_bytes(postgres_fd, counter)
        clickhouse_tmp_fd = _open_optional_dir(clickhouse_fd, "tmp")
        try:
            clickhouse_temp = (0 if clickhouse_tmp_fd is None else
                               _allocated_files(clickhouse_tmp_fd, recurse=True, counter=counter))
        finally:
            if clickhouse_tmp_fd is not None:
                os.close(clickhouse_tmp_fd)
        memory = sum(_memory_for_container(container_map[service]) for service in SERVICES)
        _need(0 <= wal_bytes <= MAX_COUNTER and 0 <= postgres_temp + clickhouse_temp <= MAX_COUNTER
              and 0 <= memory <= MAX_COUNTER, "resource_reader_failed")
        return {"disk_bytes": disk, "wal_bytes": wal_bytes,
                "temp_bytes": postgres_temp + clickhouse_temp, "memory_bytes": memory}
    finally:
        for fd in upper_fds.values():
            os.close(fd)
        for fd in volume_fds.values():
            os.close(fd)
        os.close(docker_fd)


def _reader_main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_READER_INPUT + 1)
        _need(len(raw) <= MAX_READER_INPUT, "resource_reader_failed")
        request = _strict_json(raw)
        result = _native_reader(request)
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except PermissionError:
        return 3
    except BaseException:
        return 2


if __name__ == "__main__" and sys.argv[1:] == ["--native-reader"]:
    raise SystemExit(_reader_main())
