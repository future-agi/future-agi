"""Bounded read-only host observations; never pull a helper or guess VM capacity.

The optional helper image is preprovisioned once by setup, selected by immutable
local image ID, and must contain /usr/local/bin/python3 >=3.11. Only a measured
external-containerd default layout is implemented. Other layouts return unknown.
No fallback to client df, named volumes or Docker's image-usage figures exists.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from .compatibility import decode_document
from .contracts import ReasonCode
from .host_facts import daemon_space_available
from .orchestrator import require

MAX_OUTPUT = 4 * 1024 * 1024
SOURCE_DIGESTS = {
    "daemon_storage.py": "5ff3278b2a92ea7a0fef2223c9f1c69806859504e02fae14e27ce955e547b8ef",
    "standalone.py": "4299b2283a836dfa56cc5eb68ff680c5a175250dab5fe043119c75958cd57131",
    "volume_storage.py": "b4be2088a0fdf80fa82b3d92b0dd7766f9d73706cebe0a84e2a5cef5ab69a31a",
    "migration-apps.json": "136402e5cb9ea3a6cbf401749756137924e9847af42e98a7f8f7c103632f6e38",
    "automatic-layout.json": "99910d7fbef73f3a3d976c10b9d5ff06f9c1ce566f0b31aa15bbf83cbe2b07a4",
}


class ObservationFailure(RuntimeError):
    """A closed diagnostic vocabulary; no command, stderr or sensitive values."""
    def __init__(self, code):
        self.code = code if code in {"timeout", "output_limit", "process_failed", "source_changed"} else "process_failed"
        super().__init__(self.code)


class ReadOnlyRunner:
    """Stdlib, deadline-bound subprocess.run subset with a combined output bound.

    Separate from the rollout supervisor: it cannot inherit that supervisor's
    mutating stage classification. stdin is nonblocking too. A deadline kills and
    reaps the host process group. Fixed container scripts also enforce an alarm,
    since terminating a Docker client alone does not terminate a remote exec.
    """
    def __init__(self, timeout=60, *, max_output=MAX_OUTPUT, clock=time.monotonic):
        require(0 < timeout <= 7200 and 0 < max_output <= 16 * 1024 * 1024)
        self.clock, self.deadline, self.max_output = clock, clock() + timeout, max_output

    def __call__(self, args, *, cwd=None, env=None, input=None, timeout=None,
                 capture_output=True, text=True, check=False):
        require(capture_output and not check)
        deadline = min(self.deadline, self.clock() + (timeout if timeout is not None else 300))
        if self.clock() >= deadline:
            raise ObservationFailure("timeout")
        payload = input.encode("utf-8") if isinstance(input, str) else (input or b"")
        if len(payload) > MAX_OUTPUT:
            raise ObservationFailure("output_limit")
        proc = None
        completed = False
        try:
            proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE if payload else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            buffers = {"stdout": bytearray(), "stderr": bytearray()}
            with selectors.DefaultSelector() as poll:
                for name in buffers:
                    pipe = getattr(proc, name)
                    os.set_blocking(pipe.fileno(), False)
                    poll.register(pipe, selectors.EVENT_READ, name)
                if payload:
                    os.set_blocking(proc.stdin.fileno(), False)
                    poll.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
                offset = 0
                while poll.get_map():
                    left = deadline - self.clock()
                    if left <= 0:
                        raise ObservationFailure("timeout")
                    for event, _ in poll.select(min(left, 0.1)):
                        if event.data == "stdin":
                            try:
                                offset += os.write(event.fd, payload[offset:offset + 8192])
                            except BrokenPipeError:
                                offset = len(payload)
                            if offset == len(payload):
                                poll.unregister(event.fileobj)
                                event.fileobj.close()
                        else:
                            block = os.read(event.fd, 65536)
                            if not block:
                                poll.unregister(event.fileobj)
                                continue
                            buffers[event.data].extend(block)
                            if sum(map(len, buffers.values())) > self.max_output:
                                raise ObservationFailure("output_limit")
                proc.wait(timeout=max(0.001, deadline - self.clock()))
            values = [bytes(buffers[name]) for name in ("stdout", "stderr")]
            if text:
                values = [value.decode("utf-8", "strict") for value in values]
            completed = True
            return subprocess.CompletedProcess(args, proc.returncode, *values)
        except subprocess.TimeoutExpired:
            raise ObservationFailure("timeout") from None
        except (OSError, ValueError):
            raise ObservationFailure("process_failed") from None
        finally:
            if proc is not None:
                # The parent can exit while a descendant still owns a pipe.
                # Kill the process GROUP on failure even if parent.poll() != None.
                if not completed or proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe is not None:
                        pipe.close()


def fixed_source(name):
    """Read once, check immutable shipped digest, execute those SAME bytes."""
    require(name in SOURCE_DIGESTS, ReasonCode.INITIALIZATION_REQUIRED)
    path = (Path(__file__).with_name(name) if name in ("migration-apps.json", "automatic-layout.json")
            else Path(__file__).with_name("probes") / name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not info.st_mode & 0o022
                and info.st_uid in (0, os.geteuid()) and info.st_size <= 128 * 1024,
                ReasonCode.INITIALIZATION_REQUIRED)
        with os.fdopen(os.dup(fd), "rb") as handle:
            raw = handle.read(128 * 1024 + 1)
        if hashlib.sha256(raw).hexdigest() != SOURCE_DIGESTS[name]:
            raise ObservationFailure("source_changed")
        return raw
    finally:
        os.close(fd)


def bounded_installation(installation, timeout=60, *, inherit_deadline=True):
    """Do not mutate the caller's runner/env/caches or extend its deadline."""
    cloned = copy.copy(installation)
    cloned.runner = ReadOnlyRunner(timeout)
    deadline = getattr(getattr(installation, "runner", None), "deadline", None)
    if inherit_deadline and type(deadline) in (int, float):
        cloned.runner.deadline = min(cloned.runner.deadline, deadline)
    cloned.environ = dict(installation.environ)
    cloned.env = dict(installation.env)
    cloned.env_overlay = dict(installation.env_overlay)
    cloned._config = None
    cloned._service_model = None
    cloned._service_probe = None
    return cloned


def docker_json(installation, *args, limit=MAX_OUTPUT):
    result = installation.docker_run(*args, check=False)
    require(result.returncode == 0, ReasonCode.RUNNING_VERSIONS_UNKNOWN)
    # decode_document is deliberately dict-only; inspect returns arrays.
    wrapped = b'{"result":' + result.stdout.encode("utf-8") + b'}'
    return decode_document(wrapped, limit + 16)["result"]


def checked_daemon(installation, binding):
    effective = installation.subprocess_env()
    context = binding.docker_context
    docker = list(installation.docker)
    # The registered context is pinned either on the command line, as the
    # setup, runner and CLI installations do (`docker --context <ctx>`), or
    # through DOCKER_CONTEXT with a bare `docker`. Nothing else may route.
    argv_routed = len(docker) == 3 and docker[1] == "--context" and docker[2] == context
    env_routed = len(docker) == 1
    require(bool(context) and (argv_routed or env_routed), ReasonCode.OWNERSHIP_UNCONFIRMED)
    for source in (installation.environ, effective):
        selected = source.get("DOCKER_CONTEXT")
        require(selected == context if env_routed else selected in (None, "", context),
                ReasonCode.OWNERSHIP_UNCONFIRMED)
    require(not any(effective.get(key) for key in ("DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")),
            ReasonCode.OWNERSHIP_UNCONFIRMED)
    info = docker_json(installation, "info", "--format", "{{json .}}")
    require(isinstance(info, dict), ReasonCode.OWNERSHIP_UNCONFIRMED)
    require(info.get("ID") == binding.daemon_identity and bool(binding.daemon_identity),
            ReasonCode.OWNERSHIP_UNCONFIRMED)
    return info


@dataclass(frozen=True)
class StorageFilesystem:
    role: str
    device: str
    filesystem: str
    backing_sha256: str
    available_bytes: int


@dataclass(frozen=True)
class StorageObservation:
    status: str
    available_bytes: Optional[int]
    backend: str
    evidence_sha256: Optional[str] = None
    stores: Tuple[StorageFilesystem, ...] = ()


def inspect_daemon_storage(installation, registration, *, helper_image_id=None, timeout=45):
    """No helper ID means native-only. A missing/unsupported backend is explicit.

    registration is runner_state.Registration. Setup must qualify this call
    BEFORE activating automation. It may persist the helper ID, never a mutable
    helper tag or a success assertion. This call requalifies it each time.
    """
    inst = bounded_installation(installation, timeout)
    binding = registration.binding
    name = None
    backend = "unknown"
    try:
        before = checked_daemon(inst, binding)
        if before.get("Driver") == "overlay2":
            backend = "native-overlay2"
            available = daemon_space_available(inst, binding)
            after = checked_daemon(inst, binding)
            require(all(before.get(key) == after.get(key) for key in ("Driver", "DriverStatus", "DockerRootDir", "Containerd")))
            if type(available) is int and available >= 0:
                return StorageObservation("available", available, backend)
            return StorageObservation("unsupported", None, backend)
        if not (before.get("OSType") == "linux" and before.get("Driver") == "overlayfs"
                and ["driver-type", "io.containerd.snapshotter.v1"] in before.get("DriverStatus", [])
                and before.get("DockerRootDir") == "/var/lib/docker" and type(before.get("Containerd")) is dict
                and before["Containerd"].get("Address") == "/run/containerd/containerd.sock"):
            return StorageObservation("unsupported", None, backend)
        backend = "external-containerd-overlayfs"
        if not isinstance(helper_image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", helper_image_id):
            return StorageObservation("unsupported", None, backend)
        image = docker_json(inst, "image", "inspect", helper_image_id)
        require(isinstance(image, list) and len(image) == 1 and isinstance(image[0], dict) and image[0]["Id"] == helper_image_id
                and image[0].get("Os") == "linux", ReasonCode.IMAGE_DIGEST_MISMATCH)
        source = fixed_source("daemon_storage.py")
        name = "fagi-upd-e2e-observe-daemon-" + uuid.uuid4().hex
        args = [*inst.docker, "create", "--pull", "never", "--name", name,
                "--no-healthcheck", "--restart", "no", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "16", "--memory", "64m", "--cpus", "0.5", "--user", "0:0"]
        binds = (("/proc", "/host-proc"), ("/etc/docker", "/host-docker-config"),
                 ("/etc/containerd", "/host-containerd-config"), ("/var/lib/containerd", "/daemon-store"))
        targets = {dest for _, dest in binds}
        unrelated = set((image[0].get("Config") or {}).get("Volumes") or {}) - targets
        for path in sorted(unrelated):
            require(type(path) is str and path.startswith("/") and path != "/" and "," not in path
                    and ":" not in path and ".." not in path.split("/")
                    and not any(path.startswith(dest + "/") or dest.startswith(path.rstrip("/") + "/")
                                for dest in targets), ReasonCode.UNSUPPORTED_INSTALLATION)
            args += ["--tmpfs", path + ":rw,noexec,nosuid,nodev,size=1m"]
        for src, dest in binds:
            args += ["--mount", "type=bind,src=" + src + ",dst=" + dest + ",readonly,bind-propagation=rprivate"]
        command = ["-I", "-B", "-S", "-"]
        args += ["--entrypoint", "/usr/local/bin/python3", "-i", helper_image_id, *command]
        created = inst.runner(args, cwd=str(inst.root), env=inst.subprocess_env(), text=True, capture_output=True)
        require(created.returncode == 0, ReasonCode.INITIALIZATION_REQUIRED)
        cid = created.stdout.strip()
        require(re.fullmatch(r"[0-9a-f]{64}", cid) is not None, ReasonCode.OWNERSHIP_UNCONFIRMED)
        rows = docker_json(inst, "inspect", cid)
        require(type(rows) is list and len(rows) == 1 and rows[0]["Id"] == cid
                and rows[0]["Name"] == "/" + name and rows[0]["Image"] == helper_image_id
                and (rows[0]["Config"].get("Healthcheck") or {}).get("Test") == ["NONE"]
                and rows[0]["Config"].get("Entrypoint") == ["/usr/local/bin/python3"]
                and rows[0]["Config"].get("Cmd") == command
                and set((rows[0].get('HostConfig') or {}).get('Tmpfs') or {}) == unrelated
                and not any(m.get('Type') == 'volume' for m in rows[0].get('Mounts', []))
                and rows[0]["State"]["Status"] == "created" and not rows[0]["State"]["Running"],
                ReasonCode.UNSUPPORTED_INSTALLATION)
        result = inst.runner([*inst.docker, "start", "-a", "-i", cid], cwd=str(inst.root),
                             env=inst.subprocess_env(), input=source, text=True, capture_output=True)
        require(result.returncode == 0 and not result.stderr, ReasonCode.DISK_SPACE_INSUFFICIENT)
        document = decode_document(result.stdout.encode(), 4096)
        require(set(document) == {"schema_version", "backend", "stores"}
                and type(document["schema_version"]) is int and document["schema_version"] == 1
                and document["backend"] == backend and type(document["stores"]) is dict
                and set(document["stores"]) == {"content", "snapshots"})
        values = []
        for row in document["stores"].values():
            require(type(row) is dict and set(row) == {"device", "filesystem", "backing_sha256", "available_bytes"}
                    and re.fullmatch(r"[0-9]+:[0-9]+", row["device"])
                    and row["filesystem"] in ("ext4", "xfs", "btrfs")
                    and re.fullmatch(r"[0-9a-f]{64}", row["backing_sha256"])
                    and type(row["available_bytes"]) is int and row["available_bytes"] >= 0)
            values.append(row["available_bytes"])
        after = checked_daemon(inst, binding)
        require(all(before.get(key) == after.get(key) for key in ("Driver", "DriverStatus", "DockerRootDir", "Containerd")))
        return StorageObservation("available", min(values), backend,
                                  hashlib.sha256(result.stdout.encode()).hexdigest(),
                                  tuple(StorageFilesystem(role=role, **row) for role, row in sorted(document["stores"].items())))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return StorageObservation("unknown", None, backend)
    finally:
        if name is not None:
            # Only this call's random name. A client timeout may leave a helper;
            # remote alarm is a second bound, not a reason to skip exact cleanup.
            try:
                cleanup = bounded_installation(installation, 10)
                checked_daemon(cleanup, binding)
                cleanup.docker_run("rm", "-f", name, check=False)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass


def observe_daemon_storage(installation, registration, *, helper_image_id=None, timeout=45):
    """Downloader capacity callback value: real available bytes, else None."""
    return inspect_daemon_storage(installation, registration, helper_image_id=helper_image_id,
                                  timeout=timeout).available_bytes


VOLUME_ROLES = {"app-data": "app", "postgres-data": "postgres", "clickhouse-data": "clickhouse"}
HELPER_LABEL = "ai.futureagi.readonly-observation"
# Match standalone_restore.preflight_capacity's per-member metadata allowance.
RESTORE_MEMBER_OVERHEAD_BYTES = 8192
MAX_MEASURED_BYTES = 9007199254740991


def load_fixed_data(name):
    """Reviewed installed data, protected by the SAME byte pin as fixed probes."""
    require(name in ("migration-apps.json", "automatic-layout.json"), ReasonCode.UNSUPPORTED_INSTALLATION)
    return decode_document(fixed_source(name), 128 * 1024)


def all_containers(inst):
    result = inst.docker_run("ps", "-a", "-q", "--no-trunc", check=False)
    require(result.returncode == 0, ReasonCode.OWNERSHIP_UNCONFIRMED)
    ids = result.stdout.splitlines()
    require(len(ids) <= 1000 and len(ids) == len(set(ids))
            and all(re.fullmatch(r"[0-9a-f]{64}", cid) for cid in ids), ReasonCode.OWNERSHIP_UNCONFIRMED)
    if not ids:
        return []
    rows = docker_json(inst, "inspect", *ids)
    require(type(rows) is list and len(rows) == len(ids) and {row["Id"] for row in rows} == set(ids),
            ReasonCode.OWNERSHIP_UNCONFIRMED)
    return rows


def require_owned_consumers(inst, volumes, owned_ids):
    """Include stopped/foreign/oneoff consumers and bind aliases of volume roots."""
    names = {row["Name"] for row in volumes}
    roots = [row["Mountpoint"].rstrip("/") for row in volumes]
    for record in all_containers(inst):
        for mount in record.get("Mounts", []):
            source = mount.get("Source", "").rstrip("/")
            aliases = source and any(source == root or source.startswith(root + "/")
                                     or root.startswith(source + "/") for root in roots)
            if mount.get("Name") in names or aliases:
                require(record["Id"] in owned_ids, ReasonCode.UNSUPPORTED_INSTALLATION)


def owned_volumes(inst, binding):
    config = inst.config(refresh=True)
    require(set(config.get("volumes", {})) == set(VOLUME_ROLES), ReasonCode.UNSUPPORTED_INSTALLATION)
    result = {}
    for role in VOLUME_ROLES:
        definition = config["volumes"][role]
        name = definition.get("name")
        require(type(name) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", name)
                and not definition.get("external") and not definition.get("driver_opts"), ReasonCode.UNSUPPORTED_INSTALLATION)
        rows = docker_json(inst, "volume", "inspect", name)
        require(type(rows) is list and len(rows) == 1, ReasonCode.OWNERSHIP_UNCONFIRMED)
        row = rows[0]
        labels = row.get("Labels") or {}
        require(row["Name"] == name and row.get("Driver") == "local" and not row.get("Options")
                and row.get("Scope", "local") == "local" and bool(row.get("CreatedAt"))
                and labels.get("com.docker.compose.project") == binding.compose_project
                and labels.get("com.docker.compose.volume") == role
                and type(row.get("Mountpoint")) is str and row["Mountpoint"].startswith("/")
                and ".." not in row["Mountpoint"].split("/"), ReasonCode.OWNERSHIP_UNCONFIRMED)
        result[role] = row
    return result


def run_readonly_helper(inst, binding, *, image_id, source, volume_name=None,
                        mount_target="/observed-volume", network="none", environment=None, arguments=()):
    """Create -> inspect -> start by ID, and verify exact cleanup. Never pull.

    Helpers cannot mutate stores; no automatic transaction authority is granted.
    Even image-declared VOLUMEs are masked, so no anonymous stores are created.
    All source and mount choices originate in fixed callers, not release input.
    """
    checked_daemon(inst, binding)
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None, ReasonCode.IMAGE_DIGEST_MISMATCH)
    images = docker_json(inst, "image", "inspect", image_id)
    require(type(images) is list and len(images) == 1 and images[0]["Id"] == image_id
            and images[0].get("Os") == "linux", ReasonCode.IMAGE_DIGEST_MISMATCH)
    unrelated = set((images[0].get("Config") or {}).get("Volumes") or {}) - {mount_target}
    for path in unrelated:
        require(type(path) is str and path.startswith("/") and path != "/" and "," not in path
                and ":" not in path and ".." not in path.split("/")
                and not (mount_target == path or mount_target.startswith(path.rstrip("/") + "/")
                         or path.startswith(mount_target.rstrip("/") + "/")),
                ReasonCode.UNSUPPORTED_INSTALLATION)
    # Keep read-only probes in the installation's resource namespace too.
    project = getattr(binding, "compose_project", "fagi-upd-e2e")  # Legacy daemon-only observation bindings.
    require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project) is not None,
            ReasonCode.UNSUPPORTED_INSTALLATION)
    name = project + "-observe-" + uuid.uuid4().hex
    token = uuid.uuid4().hex
    env = inst.subprocess_env()
    args = [*inst.docker, "create", "-i", "--name", name, "--label", HELPER_LABEL + "=" + token,
            "--pull", "never", "--restart", "no", "--no-healthcheck", "--read-only", "--network", network,
            "--cap-drop", "ALL", "--cap-add", "DAC_READ_SEARCH", "--security-opt", "no-new-privileges",
            "--pids-limit", "32", "--memory", "256m", "--cpus", "0.5", "--user", "0:0"]
    if volume_name is not None:
        require(type(volume_name) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", volume_name),
                ReasonCode.UNSUPPORTED_INSTALLATION)
        require(mount_target in ("/data", "/observed-volume"), ReasonCode.UNSUPPORTED_INSTALLATION)
        args += ["--mount", "type=volume,src=" + volume_name + ",dst=" + mount_target + ",readonly,volume-nocopy"]
    for path in sorted(unrelated):
        args += ["--tmpfs", path + ":rw,noexec,nosuid,nodev,size=1m"]
    for key, value in (environment or {}).items():
        require(re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) is not None)
        env[key] = value
        args += ["--env", key]
    command = ["-I", "-B", "-S", "-", *arguments]
    args += ["--entrypoint", "/usr/local/bin/python3", image_id, *command]
    cid = None
    try:
        created = inst.runner(args, cwd=str(inst.root), env=env, text=True, capture_output=True)
        require(created.returncode == 0, ReasonCode.INITIALIZATION_REQUIRED)
        cid = created.stdout.strip()
        require(re.fullmatch(r"[0-9a-f]{64}", cid) is not None, ReasonCode.OWNERSHIP_UNCONFIRMED)
        rows = docker_json(inst, "inspect", cid)
        require(type(rows) is list and len(rows) == 1, ReasonCode.OWNERSHIP_UNCONFIRMED)
        row = rows[0]
        host, config = row["HostConfig"], row["Config"]
        mounts = [m for m in row.get("Mounts", []) if m.get("Type") != "tmpfs"]
        require(row["Id"] == cid and row["Name"] == "/" + name and row["Image"] == image_id
                and config.get("Labels", {}).get(HELPER_LABEL) == token
                and config.get("Entrypoint") == ["/usr/local/bin/python3"] and config.get("Cmd") == command
                and (config.get("Healthcheck") or {}).get("Test") == ["NONE"]
                and host.get("ReadonlyRootfs") is True and host.get("NetworkMode") == network
                and host.get("RestartPolicy", {}).get("Name") == "no"
                and host.get("Privileged") is False and not host.get("Binds") and not host.get("Devices")
                and host.get("CapDrop") == ["ALL"]
                and {cap.replace("CAP_", "") for cap in host.get("CapAdd") or []} == {"DAC_READ_SEARCH"}
                and host.get("Memory") == 256 * 1024 ** 2 and host.get("PidsLimit") == 32
                and host.get("NanoCpus") == 500000000 and host.get("SecurityOpt") == ["no-new-privileges"]
                and set(host.get("Tmpfs") or {}) == unrelated
                and len(mounts) == (1 if volume_name else 0)
                and (not volume_name or (mounts[0].get("Type") == "volume" and mounts[0].get("Name") == volume_name
                     and mounts[0].get("Destination") == mount_target and mounts[0].get("RW") is False))
                and row["State"]["Status"] == "created" and not row["State"]["Running"],
                ReasonCode.UNSUPPORTED_INSTALLATION)
        response = inst.runner([*inst.docker, "start", "-a", "-i", cid], cwd=str(inst.root),
                               env=env, input=source, text=True, capture_output=True)
        final = docker_json(inst, "inspect", cid)
        require(len(final) == 1 and final[0]["Id"] == cid and not final[0]["State"]["Running"]
                and (final[0]["Config"].get("Healthcheck") or {}).get("Test") == ["NONE"]
                and not final[0]["State"].get("Health", {}).get("Log")
                and final[0]["State"]["Status"] == "exited" and not final[0]["State"].get("OOMKilled")
                and final[0]["State"]["ExitCode"] == response.returncode, ReasonCode.INITIALIZATION_REQUIRED)
        if response.returncode != 0 or response.stderr:
            try:
                error = decode_document(response.stdout.encode(), 1024)
                reason = ReasonCode(error.get("error"))
                require(reason in (ReasonCode.MIGRATION_INVENTORY_UNSUPPORTED, ReasonCode.MIGRATION_INVENTORY_MISMATCH,
                                   ReasonCode.RESTORE_CAPACITY_UNKNOWN, ReasonCode.INITIALIZATION_REQUIRED))
            except (ValueError, TypeError, RuntimeError):
                reason = ReasonCode.INITIALIZATION_REQUIRED
            require(False, reason)
        return response.stdout.encode()
    finally:
        # An independent cleanup deadline, never the expired observation runner.
        # If create timed out, reconcile ONLY the random name plus exact label.
        cleanup = bounded_installation(inst, 10, inherit_deadline=False)
        checked_daemon(cleanup, binding)
        result = cleanup.docker_run("ps", "-a", "-q", "--no-trunc", "--filter", "name=^/" + name + "$", check=False)
        require(result.returncode == 0, ReasonCode.OWNERSHIP_UNCONFIRMED)
        ids = result.stdout.splitlines()
        require(len(ids) <= 1, ReasonCode.OWNERSHIP_UNCONFIRMED)
        if ids:
            records = docker_json(cleanup, "inspect", ids[0])
            require(len(records) == 1 and records[0]["Name"] == "/" + name
                    and records[0]["Config"].get("Labels", {}).get(HELPER_LABEL) == token
                    and (cid is None or records[0]["Id"] == cid), ReasonCode.OWNERSHIP_UNCONFIRMED)
            removed = cleanup.docker_run("rm", "-f", records[0]["Id"], check=False)
            require(removed.returncode == 0, ReasonCode.OWNERSHIP_UNCONFIRMED)
        check = cleanup.docker_run("ps", "-a", "-q", "--no-trunc", "--filter", "name=^/" + name + "$", check=False)
        require(check.returncode == 0 and not check.stdout.strip(), ReasonCode.OWNERSHIP_UNCONFIRMED)


@dataclass(frozen=True)
class VolumeFilesystem:
    role: str
    name: str
    root_identity: str
    device: str
    filesystem: str
    backing_sha256: str
    free_bytes: int
    free_inodes: int
    used_bytes: Optional[int]  # allocated blocks, never the sole restore estimate
    logical_bytes: Optional[int] = None
    members: Optional[int] = None  # every pathname, including the volume root

    @property
    def allocated_bytes(self):
        return self.used_bytes

    @property
    def restore_work_bytes(self):
        """Conservative logical generation plus the restore adapter's metadata.

        Allocated bytes remain separate diagnostics and a lower bound for small
        files. Unknown logical/member dimensions cannot fall back to allocation.
        """
        require(all(type(value) is int and 0 <= value <= MAX_MEASURED_BYTES
                    for value in (self.used_bytes, self.logical_bytes, self.members))
                and self.members >= 1, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        work = max(self.used_bytes, self.logical_bytes) + self.members * RESTORE_MEMBER_OVERHEAD_BYTES
        require(work <= MAX_MEASURED_BYTES, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        return work

    @property
    def available_bytes(self):
        return self.free_bytes


@dataclass(frozen=True)
class VolumeStorageObservation:
    status: str
    volumes: Tuple[VolumeFilesystem, ...] = ()
    evidence_sha256: Optional[str] = None

    @property
    def storage_sha256(self):
        from .manifest import manifest_hash
        return manifest_hash({v.role: {"name": v.name, "root_identity": v.root_identity,
            "device": v.device, "filesystem": v.filesystem, "backing_sha256": v.backing_sha256}
            for v in self.volumes}) if self.status == "available" else None


def inspect_volume_storage(installation, registration, *, helper_image_id, timeout=60, measure_used_bytes=True):
    """Actual named-volume backing filesystems, usage, free BYTES and INODES.

    No image-store/client-checkout fallback. Each volume is sampled serially.
    Unknown/missing/unstable data is explicit, never represented by a zero size.
    root_identity is Linux st_dev:st_ino, matching the restore adapter's identity.
    """
    binding = registration.binding
    try:
        inst = bounded_installation(installation, timeout)
        before = checked_daemon(inst, binding)
        volumes = owned_volumes(inst, binding)
        owned = {row["id"] for row in inst.containers()}
        require_owned_consumers(inst, volumes.values(), owned)
        rows = []
        for role, volume in sorted(volumes.items()):
            raw = run_readonly_helper(inst, binding, image_id=helper_image_id,
                source=fixed_source("volume_storage.py"), volume_name=volume["Name"],
                arguments=("sizes" if measure_used_bytes else "identity",))
            data = decode_document(raw, 4096)
            require(set(data) == {"schema_version", "root_identity", "device", "filesystem", "backing_sha256",
                                 "free_bytes", "free_inodes", "used_bytes", "logical_bytes", "members"}
                    and type(data["schema_version"]) is int and data["schema_version"] == 2
                    and re.fullmatch(r"[0-9]+:[0-9]+", data["root_identity"])
                    and re.fullmatch(r"[0-9]+:[0-9]+", data["device"])
                    and data["filesystem"] in ("ext4", "xfs", "btrfs")
                    and re.fullmatch(r"[0-9a-f]{64}", data["backing_sha256"])
                    and all(type(data[k]) is int and 0 <= data[k] <= 9007199254740991
                            for k in ("free_bytes", "free_inodes"))
                    and (all(type(data[k]) is int and 0 <= data[k] <= MAX_MEASURED_BYTES
                             for k in ("used_bytes", "logical_bytes", "members")) and 1 <= data["members"] <= 1000000
                         if measure_used_bytes else all(data[k] is None for k in ("used_bytes", "logical_bytes", "members"))),
                    ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            del data["schema_version"]
            measured = VolumeFilesystem(role=role, name=volume["Name"], **data)
            if measure_used_bytes:
                _ = measured.restore_work_bytes  # validate the aggregate bound too
            rows.append(measured)
        require(owned_volumes(inst, binding) == volumes, ReasonCode.OWNERSHIP_UNCONFIRMED)
        require_owned_consumers(inst, volumes.values(), owned)
        after = checked_daemon(inst, binding)
        require(before == after or all(before.get(k) == after.get(k) for k in
            ("ID", "Architecture", "OSType", "Driver", "DriverStatus", "DockerRootDir", "Containerd")),
            ReasonCode.OWNERSHIP_UNCONFIRMED)
        from dataclasses import asdict
        from .manifest import manifest_hash
        return VolumeStorageObservation("available", tuple(rows), manifest_hash([asdict(row) for row in rows]))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return VolumeStorageObservation("unknown")


def inspect_backup_storage(destination):
    """Only the ENROLLED host backup directory may be passed, never proof paths.

    Return filesystem identity as diagnostics. Docker/client device numbers are
    separate namespaces: they never prove non-colocation across a remote daemon.
    """
    from .host_facts import safe_path
    path = Path(destination)
    safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info, capacity = os.fstat(fd), os.fstatvfs(fd)
        require(capacity.f_bavail >= 0 and capacity.f_favail >= 0, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        return {"free_bytes": capacity.f_bavail * capacity.f_frsize, "free_inodes": capacity.f_favail,
                "device": str(info.st_dev), "root_identity": "%d:%d" % (info.st_dev, info.st_ino)}
    finally:
        os.close(fd)
