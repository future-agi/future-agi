#!/usr/bin/env python3
"""Fail-closed two-phase runner for the 0816h DEV analogue smoke.

The wrapper deliberately splits registry discovery from the 108-cell matrix.
This host-side runner is the only reviewed path that may invoke both phases: it
binds them to one unchanged 68-key env file, validates registry evidence before
the matrix command can be reached, and captures every artifact without shell
redirection or overwrite semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

RUN_ROOT = Path("/home/ubuntu/fi-dev-qualifier-current-0816h")
CONTAINER_NAME = "fi-kartik-smoke-0816h"
HANDOFF_PATH = "/run/fi-kartik-smoke-0816h/registry-profile-handoff.json"
ENV_KEY_COUNT = 68
MAX_CONTRACT_BYTES = 2 * 1024 * 1024
MAX_PHASE_OUTPUT_BYTES = 32 * 1024 * 1024
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
LOCAL_IMAGE_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9_.-]*$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
CATALOG_DATABASE = "fi_catalog_dev_kartik_0816h"
CATALOG_EPOCH = 5
CATALOG_REVISION = 1
WINDOWS = ("30m", "1h", "6h", "24h", "7d", "30d", "90d", "180d", "365d")
PROFILES = ("default", "custom", "system", "combined")
TARGET_SHAPES = {
    "canonical_voice": ("voice",),
    "canonical_trace": ("trace", "span"),
}
SOURCE_GRANT_INVENTORY_SHA256 = (
    "0ee7e421090dd879dcd7e8cf520e45e69240dc910684948fe204bf6466e35c58"
)
SOURCE_SHOW_GRANTS_NORMALIZED_COUNT = 8
SOURCE_SHOW_GRANTS_NORMALIZED_SHA256 = (
    "238cf2b033ffba3190b029984b813348eb1d17b5674943b30b7a4c222ef8d8c8"
)
SOURCE_SYSTEM_GRANTS_CANONICAL_ROW_COUNT = 42
SOURCE_SYSTEM_GRANTS_CANONICAL_SHA256 = (
    "1404d513e2af97dafa041aba113e5542fa2f41d79d01d29e4097bfb22ad691cb"
)
SOURCE_PROBE_COUNT = 8
SOURCE_PROBES = (
    "SELECT new_id,old_id FROM futureagi.end_user_id_remap WHERE 0",
    "SELECT end_user_id,is_deleted,project_id,user_id,version "
    "FROM futureagi.end_users WHERE 0",
    "SELECT _peerdb_is_deleted,_peerdb_version,created_at,deleted,id,label_id,"
    "observation_span_id,trace_id,value FROM futureagi.model_hub_score WHERE 0",
    "SELECT 1 FROM futureagi.spans WHERE 0",
    "SELECT _peerdb_is_deleted,_peerdb_version,created_at,"
    "custom_eval_config_id,deleted,error,id,observation_span_id,output_bool,"
    "output_float,output_str,output_str_list,skipped_reason,status,trace_id "
    "FROM futureagi.tracer_eval_logger WHERE 0",
    "SELECT _version,id,is_deleted,project_id,tags FROM futureagi.traces WHERE 0",
    "SELECT dictGetOrNull('futureagi.end_users_dict','user_id',"
    "toUUID('00000000-0000-0000-0000-000000000000')),"
    "dictGetOrNull('futureagi.end_users_dict','user_id_type',"
    "toUUID('00000000-0000-0000-0000-000000000000')),"
    "dictGetOrNull('futureagi.end_users_dict','user_id_hash',"
    "toUUID('00000000-0000-0000-0000-000000000000'))",
    "SELECT active,database,min_time,table FROM system.parts WHERE 0",
)
SOURCE_PROBE_KINDS = (
    "getSetting",
    "futureagi.end_user_id_remap",
    "futureagi.end_users",
    "futureagi.model_hub_score",
    "futureagi.spans",
    "futureagi.tracer_eval_logger",
    "futureagi.traces",
    "futureagi.end_users_dict",
    "system.parts",
)
HOST_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}


class RunnerViolation(RuntimeError):
    """The phase run cannot continue without weakening its evidence contract."""


@dataclass(frozen=True)
class EnvSnapshot:
    device: int
    inode: int
    size: int
    mode: int
    uid: int
    content_sha256: str
    values: dict[str, str]

    def confidential_identity(self) -> tuple[int, int, int, int, int, str]:
        return (
            self.device,
            self.inode,
            self.size,
            self.mode,
            self.uid,
            self.content_sha256,
        )


@dataclass(frozen=True)
class PhaseCapture:
    phase: str
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_sha256: str
    payload: dict[str, Any]


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _reject_constant(value: str) -> None:
    raise RunnerViolation(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RunnerViolation("duplicate JSON object key")
        result[key] = value
    return result


def _decode_json(data: bytes) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerViolation("JSON encoding is invalid") from exc


def _read_regular(
    path: Path,
    *,
    limit: int,
    mode: int | None = None,
    owner: int | None = None,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RunnerViolation(f"cannot open required regular file: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RunnerViolation(f"required path is not regular: {path}")
        if mode is not None and stat.S_IMODE(before.st_mode) != mode:
            raise RunnerViolation(f"required file mode drifted: {path}")
        if owner is not None and before.st_uid != owner:
            raise RunnerViolation(f"required file owner drifted: {path}")
        if before.st_size > limit:
            raise RunnerViolation(f"required file exceeds size ceiling: {path}")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)

        def identity(row: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                row.st_dev,
                row.st_ino,
                row.st_size,
                row.st_mtime_ns,
                stat.S_IMODE(row.st_mode),
            )

        if len(data) > limit or identity(before) != identity(after):
            raise RunnerViolation(f"required file changed while reading: {path}")
        return data
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RunnerViolation(f"refusing to overwrite evidence: {path}") from exc
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise RunnerViolation(f"evidence file mode is not 0600: {path}")
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _capture_fd(path: Path) -> int:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RunnerViolation(f"refusing to overwrite evidence: {path}") from exc
    if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
        os.close(descriptor)
        raise RunnerViolation(f"evidence file mode is not 0600: {path}")
    return descriptor


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        + b"\n"
    )


def _env_snapshot(path: Path, expected_keys: tuple[str, ...]) -> EnvSnapshot:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RunnerViolation("cannot open the exact env file") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600:
            raise RunnerViolation("env file must be one mode-0600 regular file")
        if before.st_uid != os.geteuid() or before.st_size > 1024 * 1024:
            raise RunnerViolation("env file ownership or size drifted")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 128 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)

        def stable(row: os.stat_result) -> tuple[int, int, int, int, int, int]:
            return (
                row.st_dev,
                row.st_ino,
                row.st_size,
                row.st_mtime_ns,
                stat.S_IMODE(row.st_mode),
                row.st_uid,
            )

        if stable(before) != stable(after) or len(data) != before.st_size:
            raise RunnerViolation("env file changed while reading")
    finally:
        os.close(descriptor)
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise RunnerViolation("env file is not strict UTF-8") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith(("#", "export ")) or "=" not in line:
            raise RunnerViolation("env file contains unsupported syntax")
        key, value = line.split("=", 1)
        if ENV_KEY_RE.fullmatch(key) is None or key in values or "\x00" in value:
            raise RunnerViolation("env file key contract drifted")
        values[key] = value
    if tuple(sorted(values)) != expected_keys:
        raise RunnerViolation("env file does not contain the exact reviewed key set")
    return EnvSnapshot(
        device=before.st_dev,
        inode=before.st_ino,
        size=before.st_size,
        mode=stat.S_IMODE(before.st_mode),
        uid=before.st_uid,
        content_sha256=_sha256(data),
        values=values,
    )


def _assert_same_env(expected: EnvSnapshot, actual: EnvSnapshot) -> None:
    if expected.confidential_identity() != actual.confidential_identity():
        raise RunnerViolation(
            "env inode, size, mode, owner, or confidential content changed"
        )


def _expected_paths() -> dict[str, Path]:
    evidence = RUN_ROOT / "evidence"
    return {
        "contract": RUN_ROOT / "bundle" / "kartik-smoke-0816h-run-contract.json",
        "wrapper": RUN_ROOT / "bundle" / "kartik-smoke-0816h.py",
        "runner": RUN_ROOT / "bundle" / "phase-runner-0816h.py",
        "env": RUN_ROOT / "run" / "kartik-smoke-0816h.env",
        "attestation": evidence / "kartik-smoke-0816h.env-keys.json",
        **{
            f"{phase}.{kind}": evidence / f"kartik-smoke-0816h.{phase}.{suffix}"
            for phase in ("registry", "matrix")
            for kind, suffix in (
                ("stdout", "stdout.json"),
                ("stderr", "stderr.bin"),
                ("exit", "exit-code"),
                ("argv", "argv.json"),
            )
        },
    }


def _validate_directories() -> None:
    for path in (
        RUN_ROOT,
        RUN_ROOT / "bundle",
        RUN_ROOT / "run",
        RUN_ROOT / "evidence",
    ):
        try:
            info = path.lstat()
        except OSError as exc:
            raise RunnerViolation(
                f"required runner directory is absent: {path}"
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise RunnerViolation(f"runner directory ownership or mode drifted: {path}")


def _phase_paths(contract: dict[str, Any], phase: str) -> dict[str, Path]:
    try:
        section = contract["phase_execution"][phase]
        result = {
            "stdout": Path(section["stdout_path"]),
            "stderr": Path(section["stderr_path"]),
            "exit": Path(section["exit_code_path"]),
            "argv": Path(section["argv_record_path"]),
        }
    except (KeyError, TypeError) as exc:
        raise RunnerViolation(f"{phase} evidence paths are invalid") from exc
    expected = _expected_paths()
    if any(result[key] != expected[f"{phase}.{key}"] for key in result):
        raise RunnerViolation(f"{phase} evidence path drifted")
    return result


def _expected_phase_argv(keys: tuple[str, ...], phase: str) -> list[str]:
    if phase not in {"registry", "matrix"}:
        raise RunnerViolation("unknown phase argv requested")
    env_path = _expected_paths()["env"]
    code = (
        "import os,sys;os.dup2(os.open(os.devnull,os.O_WRONLY),2);"
        f"K={keys!r};E={{k:os.environ[k] for k in K}};"
        "os.execve(sys.executable,(sys.executable,"
        "'/run/fi-kartik-smoke-0816h/kartik-smoke-0816h.py',"
        f"'--phase','{phase}'),E)"
    )
    return [
        "docker",
        "exec",
        "--env-file",
        str(env_path),
        "--workdir",
        "/app/backend",
        CONTAINER_NAME,
        "python",
        "-c",
        code,
    ]


def _validate_contract(path: Path) -> tuple[dict[str, Any], tuple[str, ...], Path]:
    _validate_directories()
    expected = _expected_paths()
    if path != expected["contract"]:
        raise RunnerViolation("contract path is not the reviewed 0816h bundle path")
    contract = _decode_json(
        _read_regular(path, limit=MAX_CONTRACT_BYTES, mode=0o600, owner=os.geteuid())
    )
    if not isinstance(contract, dict):
        raise RunnerViolation("run contract must be one JSON object")
    if "__PENDING_0816H_" in json.dumps(contract, sort_keys=True):
        raise RunnerViolation("run contract still contains assembly placeholders")
    state = contract.get("binding_state") or {}
    if (
        contract.get("status") != "BOUND_AUDITED_DEV_GO"
        or contract.get("environment") != "DEV"
        or contract.get("execution_authorized") is not True
        or state.get("state") != "BOUND_AUDITED_DEV_GO"
        or not _exact_int(state.get("placeholder_count_remaining"), 0)
        or state.get("independent_static_and_runtime_preflight_audit_passed")
        is not True
        or state.get("human_DEV_approval_recorded") is not True
    ):
        raise RunnerViolation("bound, audited DEV authorization is absent")
    execution = contract.get("execution") or {}
    if execution.get("approval_required") is not False or any(
        execution.get(key) is not False
        for key in (
            "performed",
            "container_started",
            "database_authenticated",
            "callbacks_invoked",
            "registry_phase_invoked",
            "matrix_phase_invoked",
        )
    ):
        raise RunnerViolation("execution state is not a fresh approved DEV run")
    minimal = contract.get("minimal_environment") or {}
    keys = minimal.get("exact_keys")
    if (
        not isinstance(keys, list)
        or not all(isinstance(key, str) for key in keys)
        or tuple(keys) != tuple(sorted(set(keys)))
        or len(keys) != ENV_KEY_COUNT
        or not _exact_int(minimal.get("key_count"), ENV_KEY_COUNT)
        or minimal.get("sorted_key_sha256") != _sha256("\n".join(keys))
        or minimal.get("all_other_keys_forbidden") is not True
    ):
        raise RunnerViolation("68-key environment contract drifted")
    pins = contract.get("pins") or {}
    hash_pins = (
        "bundle_manifest_sha256",
        "canonical_tenant_binding_sha256",
        "canonical_trace_project_uuid_sha256",
        "canonical_voice_project_uuid_sha256",
        "catalog_activation_sha256",
        "catalog_activation_source_manifest_sha256",
        "dockerfile_sha256",
        "excluded_project_uuid_sha256",
        "harness_sha256",
        "job_template_sha256",
        "phase_runner_sha256",
        "qualifier_sha256",
        "runtime_overlay_sha256",
        "source_grant_inventory_sha256",
        "source_show_grants_normalized_sha256",
        "source_manifest_sha256",
        "source_system_grants_canonical_sha256",
        "wrapper_sha256",
    )
    if any(HASH_RE.fullmatch(str(pins.get(key) or "")) is None for key in hash_pins):
        raise RunnerViolation("immutable hash pin is absent or invalid")
    if re.fullmatch(r"^[0-9a-f]{40}$", str(pins.get("base_commit") or "")) is None:
        raise RunnerViolation("immutable base commit pin is absent or invalid")
    if IMAGE_ID_RE.fullmatch(str(pins.get("image_id") or "")) is None:
        raise RunnerViolation("immutable local image ID pin is invalid")
    if (
        LOCAL_IMAGE_TAG_RE.fullmatch(str(pins.get("local_nonimmutable_tag") or ""))
        is None
    ):
        raise RunnerViolation("local image tag pin is invalid")
    if (
        pins.get("catalog_database") != CATALOG_DATABASE
        or not _exact_int(pins.get("catalog_epoch"), CATALOG_EPOCH)
        or not _exact_int(pins.get("catalog_revision"), CATALOG_REVISION)
    ):
        raise RunnerViolation("catalog database or activation revision pin drifted")
    expected_source_pins = {
        "source_grant_inventory_sha256": SOURCE_GRANT_INVENTORY_SHA256,
        "source_show_grants_normalized_sha256": (SOURCE_SHOW_GRANTS_NORMALIZED_SHA256),
        "source_system_grants_canonical_sha256": (
            SOURCE_SYSTEM_GRANTS_CANONICAL_SHA256
        ),
    }
    if any(pins.get(key) != value for key, value in expected_source_pins.items()):
        raise RunnerViolation("source-role closure hash pin drifted")
    database_contract = contract.get("database_contract") or {}
    source_inventory = database_contract.get("source_grant_inventory")
    admin_preflight = database_contract.get("source_admin_grant_preflight") or {}
    if (
        not isinstance(source_inventory, dict)
        or _sha256(_canonical_json(source_inventory)[:-1])
        != SOURCE_GRANT_INVENTORY_SHA256
        or database_contract.get("source_grant_inventory_sha256")
        != pins["source_grant_inventory_sha256"]
        or type(database_contract.get("source_probe_count")) is not int
        or database_contract["source_probe_count"] != SOURCE_PROBE_COUNT
        or database_contract.get("source_probes") != list(SOURCE_PROBES)
        or database_contract.get("source_probe_kinds") != list(SOURCE_PROBE_KINDS)
        or type(database_contract.get("source_show_grants_normalized_count")) is not int
        or database_contract["source_show_grants_normalized_count"]
        != SOURCE_SHOW_GRANTS_NORMALIZED_COUNT
        or admin_preflight.get("required_before_execution") is not True
        or type(admin_preflight.get("show_grants_normalized_count")) is not int
        or admin_preflight["show_grants_normalized_count"]
        != SOURCE_SHOW_GRANTS_NORMALIZED_COUNT
        or admin_preflight.get("show_grants_normalized_sha256")
        != pins["source_show_grants_normalized_sha256"]
        or type(admin_preflight.get("system_grants_canonical_row_count")) is not int
        or admin_preflight["system_grants_canonical_row_count"]
        != SOURCE_SYSTEM_GRANTS_CANONICAL_ROW_COUNT
        or admin_preflight.get("system_grants_canonical_sha256")
        != pins["source_system_grants_canonical_sha256"]
        or type(admin_preflight.get("role_grants_count")) is not int
        or admin_preflight["role_grants_count"] != 0
        or admin_preflight.get("principal_redacted") is not True
    ):
        raise RunnerViolation("source-role closure contract drifted")
    fixed = minimal.get("fixed_pin_values") or {}
    expected_fixed = {
        "EXPECTED_BASE_COMMIT": pins["base_commit"],
        "EXPECTED_IMAGE_ID": pins["image_id"],
        "EXPECTED_KARTIK_SMOKE_0816H_SHA256": pins["wrapper_sha256"],
        "EXPECTED_QUALIFIER_SHA256": pins["qualifier_sha256"],
        "EXPECTED_SOURCE_MANIFEST_SHA256": pins["source_manifest_sha256"],
        "KARTIK_EXCLUDED_PROJECT_UUID_SHA256": pins["excluded_project_uuid_sha256"],
        "KARTIK_SMOKE_EVIDENCE_LABEL": "0816h",
        "KARTIK_SMOKE_SOURCE_AUTH_IPV4": "172.19.255.250",
        "PROPERTY_CATALOG_CH_DATABASE": CATALOG_DATABASE,
        "PROPERTY_CATALOG_DATABASE": CATALOG_DATABASE,
    }
    if any(fixed.get(key) != value for key, value in expected_fixed.items()):
        raise RunnerViolation("contract fixed environment pins drifted")
    if Path(minimal.get("host_env_file", "")) != expected["env"]:
        raise RunnerViolation("contract env-file path drifted")
    if (
        Path(contract["output_capture"]["env_key_attestation_path"])
        != expected["attestation"]
    ):
        raise RunnerViolation("env-key attestation path drifted")
    retained = contract.get("cleanup_plan", {}).get("retain_mode_0600")
    required_retained = {
        str(expected[key])
        for key in (
            "contract",
            "wrapper",
            "runner",
            "attestation",
            "registry.stdout",
            "registry.stderr",
            "registry.exit",
            "registry.argv",
            "matrix.stdout",
            "matrix.stderr",
            "matrix.exit",
            "matrix.argv",
        )
    }
    if (
        not isinstance(retained, list)
        or not all(isinstance(value, str) for value in retained)
        or not required_retained.issubset(set(retained))
    ):
        raise RunnerViolation("mode-0600 review evidence retention set drifted")
    container = contract["container"]
    if container.get("name") != CONTAINER_NAME:
        raise RunnerViolation("container name drifted")
    if (
        container.get("image_pull_policy") != "never"
        or container.get("repo_digests_expected") != []
        or container.get("image_must_match_local_tag_and_id") is not True
    ):
        raise RunnerViolation("local-only image execution contract drifted")
    if contract["phase_execution"].get("order") != [
        "registry",
        "inspect_and_bind_registry_result",
        "matrix",
        "inspect_and_cross_bind_matrix_result",
    ]:
        raise RunnerViolation("phase order drifted")
    for phase in ("registry", "matrix"):
        _phase_paths(contract, phase)
        argv = contract["phase_execution"][phase].get("exact_docker_exec_argv")
        if (
            not isinstance(argv, list)
            or not all(isinstance(value, str) and "\x00" not in value for value in argv)
            or argv != _expected_phase_argv(tuple(keys), phase)
        ):
            raise RunnerViolation(f"{phase} exact argv drifted")
    wrapper = _read_regular(
        expected["wrapper"],
        limit=2 * 1024 * 1024,
        mode=0o600,
        owner=os.geteuid(),
    )
    if b"__PENDING_0816H_" in wrapper:
        raise RunnerViolation("host wrapper still contains assembly placeholders")
    if _sha256(wrapper) != pins["wrapper_sha256"]:
        raise RunnerViolation("host wrapper hash does not match the bound contract")
    if Path(__file__).resolve() != expected["runner"]:
        raise RunnerViolation(
            "phase runner is not executing from its reviewed bundle path"
        )
    runner_bytes = _read_regular(
        expected["runner"],
        limit=2 * 1024 * 1024,
        mode=0o600,
        owner=os.geteuid(),
    )
    if _sha256(runner_bytes) != pins["phase_runner_sha256"]:
        raise RunnerViolation("phase runner hash does not match the bound contract")
    return contract, tuple(keys), expected["env"]


def _parse_single_record(data: bytes) -> dict[str, Any]:
    if not data.endswith(b"\n"):
        raise RunnerViolation("phase stdout is not one newline-terminated JSON record")
    payload = _decode_json(data[:-1])
    if not isinstance(payload, dict):
        raise RunnerViolation("phase stdout JSON is not an object")
    return payload


def _execute_phase(
    contract: dict[str, Any],
    phase: str,
    *,
    popen_factory: Callable[..., Any] = subprocess.Popen,
) -> PhaseCapture:
    paths = _phase_paths(contract, phase)
    argv = list(contract["phase_execution"][phase]["exact_docker_exec_argv"])
    _write_exclusive(
        paths["argv"],
        _canonical_json(
            {
                "schema": "fi-kartik-phase-argv/0816h/v1",
                "phase": phase,
                "argv": argv,
                "shell": False,
            }
        ),
    )
    stdout_fd = _capture_fd(paths["stdout"])
    try:
        stderr_fd = _capture_fd(paths["stderr"])
    except BaseException:
        os.close(stdout_fd)
        raise
    try:
        with open(os.devnull, "rb") as devnull:
            process = popen_factory(
                argv,
                shell=False,
                stdin=devnull,
                stdout=stdout_fd,
                stderr=stderr_fd,
                close_fds=True,
                env=dict(HOST_ENV),
            )
            returncode = int(process.wait())
        os.fsync(stdout_fd)
        os.fsync(stderr_fd)
    finally:
        os.close(stdout_fd)
        os.close(stderr_fd)
    _write_exclusive(paths["exit"], f"{returncode}\n".encode("ascii"))
    stdout = _read_regular(
        paths["stdout"],
        limit=MAX_PHASE_OUTPUT_BYTES,
        mode=0o600,
        owner=os.geteuid(),
    )
    stderr = _read_regular(
        paths["stderr"],
        limit=MAX_PHASE_OUTPUT_BYTES,
        mode=0o600,
        owner=os.geteuid(),
    )
    payload = _parse_single_record(stdout)
    return PhaseCapture(phase, returncode, stdout, stderr, _sha256(stdout), payload)


def _validate_source_role_closure(
    payload: dict[str, Any], phase: str, contract: dict[str, Any]
) -> None:
    pins = contract["pins"]
    database_contract = contract.get("database_contract") or {}
    audit = payload.get("database_identity_audit") or {}
    source = audit.get("source") or {}
    closure = source.get("grant_closure") or {}
    query_evidence = payload.get("query_kinds") or {}
    inventory = query_evidence.get("source_grant_inventory")
    if (
        type(source.get("probe_count")) is not int
        or source["probe_count"] != SOURCE_PROBE_COUNT
        or source.get("probe_kinds") != list(SOURCE_PROBE_KINDS)
        or closure.get("grant_inventory_sha256")
        != pins["source_grant_inventory_sha256"]
        or type(closure.get("show_grants_normalized_count")) is not int
        or closure["show_grants_normalized_count"]
        != SOURCE_SHOW_GRANTS_NORMALIZED_COUNT
        or closure.get("show_grants_normalized_sha256")
        != pins["source_show_grants_normalized_sha256"]
        or type(closure.get("active_role_count")) is not int
        or closure["active_role_count"] != 0
        or type(query_evidence.get("source_probe_count")) is not int
        or query_evidence["source_probe_count"] != SOURCE_PROBE_COUNT
        or query_evidence.get("source_probe_kinds") != list(SOURCE_PROBE_KINDS)
        or inventory != database_contract.get("source_grant_inventory")
        or query_evidence.get("source_grant_inventory_sha256")
        != pins["source_grant_inventory_sha256"]
        or not isinstance(inventory, dict)
        or _sha256(_canonical_json(inventory)[:-1])
        != pins["source_grant_inventory_sha256"]
    ):
        raise RunnerViolation(f"{phase} source-role closure evidence drifted")


def _validate_common(
    payload: dict[str, Any], phase: str, contract: dict[str, Any]
) -> None:
    pins = contract["pins"]
    if (
        payload.get("schema") != "fi-kartik-dev-analogue-functional-smoke/0816h/v1"
        or payload.get("phase") != phase
        or payload.get("environment") != "DEV"
        or payload.get("evidence_label") != "0816h"
        or payload.get("select_only") is not True
        or payload.get("production_touched") is not False
        or payload.get("release_qualified") is not False
        or payload.get("release_qualification_attempted") is not False
        or payload.get("named_target_matrix_executed") is not False
        or payload.get("functional_smoke_passed") is not True
        or payload.get("coverage_complete") is not True
        or not _exact_int(payload.get("coverage_exit_code"), 0)
        or not _exact_int(payload.get("exit_code"), 0)
        or payload.get("error") is not None
        or payload.get("route_failures") != []
        or not _exact_int(payload.get("required_population_gap_count"), 0)
        or payload.get("canonical_tenant_binding_sha256")
        != pins["canonical_tenant_binding_sha256"]
    ):
        raise RunnerViolation(f"{phase} result failed its common acceptance contract")
    source = payload.get("source_identity") or {}
    if (
        source.get("base_commit") != pins["base_commit"]
        or source.get("derived_image_id") != pins["image_id"]
        or source.get("local_image_tag") != pins["local_nonimmutable_tag"]
        or source.get("source_manifest_sha256") != pins["source_manifest_sha256"]
        or source.get("qualifier_sha256") != pins["qualifier_sha256"]
    ):
        raise RunnerViolation(f"{phase} source identity is not bound to the contract")
    _validate_source_role_closure(payload, phase, contract)
    targets = payload.get("targets")
    expected_hashes = {
        "canonical_voice": pins["canonical_voice_project_uuid_sha256"],
        "canonical_trace": pins["canonical_trace_project_uuid_sha256"],
    }
    if not isinstance(targets, dict) or set(targets) != set(expected_hashes):
        raise RunnerViolation(f"{phase} canonical target set drifted")
    for label, project_hash in expected_hashes.items():
        target = targets[label]
        population = target.get("project_catalog_population") or {}
        if (
            target.get("project_id_sha256") != project_hash
            or target.get("workspace_catalog_admitted") is not True
            or population.get("workspace_admitted") is not True
            or population.get("project_population_expected") is not True
            or type(population.get("live_definition_count")) is not int
            or population["live_definition_count"] < 1
            or type(population.get("live_value_count")) is not int
            or population["live_value_count"] < 1
            or population.get("activation_source_manifest_sha256")
            != pins["catalog_activation_source_manifest_sha256"]
            or population.get("activation_sha256") != pins["catalog_activation_sha256"]
            or not _exact_int(
                population.get("active_catalog_epoch"), pins["catalog_epoch"]
            )
            or not _exact_int(
                population.get("active_catalog_revision"), pins["catalog_revision"]
            )
            or HASH_RE.fullmatch(str(population.get("activation_binding_sha256") or ""))
            is None
        ):
            raise RunnerViolation(f"{phase} {label} catalog binding is invalid")
    excluded = payload.get("excluded_target") or {}
    if (
        excluded.get("selected") is not False
        or excluded.get("exclusion_digest_bound") is not True
        or excluded.get("uuid_sha256_pin") != pins["excluded_project_uuid_sha256"]
        or any(
            not _exact_int(excluded.get(key), 0)
            for key in (
                "target_selection_count",
                "pg_query_count",
                "catalog_query_count",
                "client_count",
                "callback_count",
                "profile_count",
                "matrix_cell_count",
                "target_profile_handoff_entry_count",
                "raw_identity_handoff_entry_count",
            )
        )
    ):
        raise RunnerViolation(f"{phase} excluded-project proof drifted")


def _validate_registry(capture: PhaseCapture, contract: dict[str, Any]) -> str:
    if not _exact_int(capture.returncode, 0) or capture.stderr != b"":
        raise RunnerViolation("registry process or stderr acceptance failed")
    payload = capture.payload
    _validate_common(payload, "registry", contract)
    registry = payload.get("registry") or {}
    matrix = payload.get("matrix") or {}
    handoff = registry.get("handoff_sha256")
    targets = payload.get("targets") or {}

    def model_value_evidence_green(label: str) -> bool:
        target = targets.get(label) or {}
        evidence = target.get("model_values") or {}
        return (
            isinstance(evidence, dict)
            and evidence.get("qualified") is True
            and evidence.get("catalog_read_mode") == "read"
            and type(evidence.get("page_size")) is int
            and evidence["page_size"] == 1
            and type(evidence.get("p1_values")) is int
            and evidence["p1_values"] == 1
            and type(evidence.get("p2_values")) is int
            and evidence["p2_values"] == 1
            and evidence.get("continuation_exercised") is True
            and evidence.get("search_proven") is True
            and type(evidence.get("lookback_frozen_baseline_days")) is int
            and evidence["lookback_frozen_baseline_days"] == 7
            and type(evidence.get("lookback_effective_days")) is int
            and evidence["lookback_effective_days"] == 366
            and evidence.get("lookback_restored_on_return") is True
        )

    if (
        registry.get("executed") is not True
        or registry.get("passed") is not True
        or registry.get("handoff_created") is not True
        or HASH_RE.fullmatch(str(handoff or "")) is None
        or matrix.get("executed") is not False
        or not _exact_int(matrix.get("expected_cell_count"), 108)
        or not all(
            model_value_evidence_green(label)
            for label in ("canonical_voice", "canonical_trace")
        )
        or not _timings_green(
            payload.get("timings_by_route"),
            {"property_keys", "filter_values", "metrics"},
        )
    ):
        raise RunnerViolation("registry result did not satisfy the matrix start gate")
    return str(handoff)


def _expected_cell_identities() -> set[tuple[str, str, str, str]]:
    return {
        (target, window, kind, profile)
        for target, kinds in TARGET_SHAPES.items()
        for window in WINDOWS
        for kind in kinds
        for profile in PROFILES
    }


def _timings_green(value: Any, expected_routes: set[str]) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == expected_routes
        and all(
            isinstance(route, dict)
            and route.get("under_9_8s") is True
            and isinstance(route.get("max_s"), (int, float))
            and not isinstance(route.get("max_s"), bool)
            and 0 <= route["max_s"] < 9.8
            and type(route.get("callbacks")) is int
            and route["callbacks"] > 0
            and isinstance(route.get("statuses"), dict)
            and set(route["statuses"]) == {"200"}
            and type(route["statuses"]["200"]) is int
            and route["statuses"]["200"] == route["callbacks"]
            for route in value.values()
        )
    )


def _validate_matrix(capture: PhaseCapture, contract: dict[str, Any]) -> str:
    if not _exact_int(capture.returncode, 0) or capture.stderr != b"":
        raise RunnerViolation("matrix process or stderr acceptance failed")
    payload = capture.payload
    _validate_common(payload, "matrix", contract)
    registry = payload.get("registry") or {}
    matrix = payload.get("matrix") or {}
    cells = matrix.get("cells")
    if not isinstance(cells, list):
        raise RunnerViolation("matrix cells are absent")
    identities: list[tuple[str, str, str, str]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            raise RunnerViolation("matrix cell is not an object")
        identity = tuple(
            cell.get(key) for key in ("target", "window", "kind", "profile")
        )
        if not all(isinstance(value, str) for value in identity):
            raise RunnerViolation("matrix cell identity is invalid")
        identities.append(identity)  # type: ignore[arg-type]
        if cell.get("passed") is not True:
            raise RunnerViolation("matrix contains a failed cell")
    timings = payload.get("timings_by_route") or {}
    expected_shapes = {
        "canonical_voice": {
            "kinds": ["voice"],
            "profiles": list(PROFILES),
        },
        "canonical_trace": {
            "kinds": ["trace", "span"],
            "profiles": list(PROFILES),
        },
    }
    positive_count = matrix.get("positive_cell_count")
    continuation_count = matrix.get("continuation_cell_count")
    if (
        registry.get("executed") is not False
        or registry.get("prerequisite_verified") is not True
        or registry.get("handoff_loaded") is not True
        or HASH_RE.fullmatch(str(registry.get("handoff_sha256") or "")) is None
        or payload.get("analogue_matrix_executed") is not True
        or matrix.get("executed") is not True
        or matrix.get("windows") != list(WINDOWS)
        or matrix.get("shapes") != expected_shapes
        or not _exact_int(matrix.get("expected_cell_count"), 108)
        or not _exact_int(matrix.get("executed_cell_count"), 108)
        or not _exact_int(matrix.get("passed_cell_count"), 108)
        or type(positive_count) is not int
        or not 12 <= positive_count <= 108
        or type(continuation_count) is not int
        or not 12 <= continuation_count <= 108
        or len(identities) != 108
        or len(set(identities)) != 108
        or set(identities) != _expected_cell_identities()
        or not _timings_green(timings, {"trace_list", "span_list", "voice_list"})
    ):
        raise RunnerViolation("matrix result is not the exact green 108-cell set")
    return str(registry["handoff_sha256"])


def _cross_binding(payload: dict[str, Any]) -> dict[str, Any]:
    target_bindings = {}
    for label in sorted(TARGET_SHAPES):
        target = payload["targets"][label]
        population = target["project_catalog_population"]
        target_bindings[label] = {
            "project_id_sha256": target["project_id_sha256"],
            **{
                key: population.get(key)
                for key in (
                    "live_definition_count",
                    "live_value_count",
                    "active_catalog_epoch",
                    "active_catalog_revision",
                    "lineage_anchor_revision",
                    "projection_version",
                    "activation_sequence",
                    "activation_source_manifest_sha256",
                    "activation_sha256",
                    "activation_binding_sha256",
                )
            },
        }
    source = payload["source_identity"]
    return {
        "run_id": payload.get("run_id"),
        "frozen_end": payload.get("frozen_end"),
        "source_auth_ipv4_sha256": payload.get("source_auth_ipv4_sha256"),
        "canonical_tenant_binding_sha256": payload.get(
            "canonical_tenant_binding_sha256"
        ),
        "source_identity": {
            key: source.get(key)
            for key in (
                "base_commit",
                "derived_image_id",
                "local_image_tag",
                "base_image_digest",
                "source_manifest_sha256",
                "qualifier_sha256",
                "verified_runtime_files",
                "verified_runtime_deletions",
                "dirty_file_count",
                "dirty_runtime_file_count",
            )
        },
        "database_identity_audit": payload.get("database_identity_audit"),
        "source_role_query_evidence": {
            key: (payload.get("query_kinds") or {}).get(key)
            for key in (
                "source_probe_count",
                "source_probe_kinds",
                "source_grant_inventory",
                "source_grant_inventory_sha256",
            )
        },
        "targets": target_bindings,
        "excluded_project_uuid_sha256": payload["excluded_target"].get(
            "uuid_sha256_pin"
        ),
    }


def _probe_handoff(*, expect_present: bool) -> str | None:
    code = """import hashlib,json,os,stat,sys
p=sys.argv[1]
expected=sys.argv[2]=='present'
present=os.path.lexists(p)
assert present is expected
data=b''
if present:
    fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        before=os.fstat(fd)
        assert stat.S_ISREG(before.st_mode)
        assert stat.S_IMODE(before.st_mode)==0o600
        assert before.st_size<=1048576
        chunks=[]
        while True:
            chunk=os.read(fd,131072)
            if not chunk:
                break
            chunks.append(chunk)
        data=b''.join(chunks)
        after=os.fstat(fd)
        assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
        assert len(data)==before.st_size
    finally:
        os.close(fd)
print(json.dumps({'present':present,'sha256':hashlib.sha256(data).hexdigest() if present else None},sort_keys=True,separators=(',',':')))
"""
    try:
        completed = subprocess.run(
            [
                "docker",
                "exec",
                "--workdir",
                "/app/backend",
                CONTAINER_NAME,
                "python",
                "-I",
                "-S",
                "-c",
                code,
                HANDOFF_PATH,
                "present" if expect_present else "absent",
            ],
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            close_fds=True,
            env=dict(HOST_ENV),
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerViolation("container handoff probe could not run") from exc
    if (
        not _exact_int(completed.returncode, 0)
        or completed.stderr
        or len(completed.stdout) > 4096
    ):
        raise RunnerViolation("container handoff probe failed")
    payload = _decode_json(completed.stdout)
    if not isinstance(payload, dict) or payload.get("present") is not expect_present:
        raise RunnerViolation("container handoff presence proof drifted")
    digest = payload.get("sha256")
    if expect_present and HASH_RE.fullmatch(str(digest or "")) is None:
        raise RunnerViolation("container handoff digest proof drifted")
    if not expect_present and digest is not None:
        raise RunnerViolation("destroyed handoff unexpectedly retained a digest")
    return str(digest) if expect_present else None


def _inspect_scalar(argv: list[str], *, field: str) -> str:
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            close_fds=True,
            env=dict(HOST_ENV),
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerViolation(f"Docker {field} inspection could not run") from exc
    try:
        value = completed.stdout.decode("ascii", "strict").strip()
    except UnicodeDecodeError as exc:
        raise RunnerViolation(f"Docker {field} inspection was not ASCII") from exc
    if (
        not _exact_int(completed.returncode, 0)
        or completed.stderr
        or not value
        or "\n" in value
        or len(value) > 4096
    ):
        raise RunnerViolation(f"Docker {field} inspection failed")
    return value


def _inspect_local_image_binding(contract: dict[str, Any]) -> dict[str, Any]:
    pins = contract["pins"]
    tag = str(pins["local_nonimmutable_tag"])
    image_id = str(pins["image_id"])
    inspected_id = _inspect_scalar(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        field="local image ID",
    )
    repo_digests = _decode_json(
        _inspect_scalar(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                tag,
            ],
            field="local image RepoDigests",
        ).encode("ascii")
    )
    container_image_id = _inspect_scalar(
        ["docker", "inspect", "--format", "{{.Image}}", CONTAINER_NAME],
        field="container image ID",
    )
    container_image_tag = _inspect_scalar(
        ["docker", "inspect", "--format", "{{.Config.Image}}", CONTAINER_NAME],
        field="container configured image",
    )
    if (
        inspected_id != image_id
        or container_image_id != image_id
        or container_image_tag not in {tag, image_id}
        or repo_digests != []
    ):
        raise RunnerViolation("container is not bound to the exact local-only image")
    return {
        "image_id": image_id,
        "local_image_tag": tag,
        "repo_digests": [],
        "pull_policy": "never",
    }


def run_contract(
    contract_path: Path,
    *,
    execute_phase: Callable[[dict[str, Any], str], PhaseCapture] = _execute_phase,
    probe_handoff: Callable[..., str | None] = _probe_handoff,
    inspect_image_binding: Callable[[dict[str, Any]], dict[str, Any]] = (
        _inspect_local_image_binding
    ),
) -> dict[str, Any]:
    contract, keys, env_path = _validate_contract(contract_path)
    image_binding = inspect_image_binding(contract)
    initial = _env_snapshot(env_path, keys)
    fixed = contract["minimal_environment"]["fixed_pin_values"]
    if any(initial.values.get(key) != value for key, value in fixed.items()):
        raise RunnerViolation("env file does not match the contract fixed pins")
    registry = execute_phase(contract, "registry")
    registry_handoff = _validate_registry(registry, contract)
    _assert_same_env(initial, _env_snapshot(env_path, keys))
    probed_handoff = probe_handoff(expect_present=True)
    if probed_handoff != registry_handoff:
        raise RunnerViolation("registry result and sealed handoff digest differ")
    _write_exclusive(
        _expected_paths()["attestation"],
        _canonical_json(
            {
                "schema": "fi-kartik-env-key-attestation/0816h/v1",
                "key_count": len(keys),
                "sorted_key_sha256": _sha256("\n".join(keys)),
                "keys": list(keys),
                "env_file": {
                    "device": initial.device,
                    "inode": initial.inode,
                    "size": initial.size,
                    "mode": "0600",
                    "confidential_content_sha256_recorded": False,
                },
                "registry_stdout_sha256": registry.stdout_sha256,
                "registry_handoff_sha256": registry_handoff,
            }
        ),
    )
    _assert_same_env(initial, _env_snapshot(env_path, keys))
    matrix = execute_phase(contract, "matrix")
    _assert_same_env(initial, _env_snapshot(env_path, keys))
    if probe_handoff(expect_present=False) is not None:
        raise RunnerViolation("matrix did not destroy the sealed registry handoff")
    matrix_handoff = _validate_matrix(matrix, contract)
    if matrix_handoff != registry_handoff:
        raise RunnerViolation("matrix did not bind the exact registry handoff")
    if _cross_binding(registry.payload) != _cross_binding(matrix.payload):
        raise RunnerViolation("registry and matrix immutable result bindings differ")
    return {
        "schema": "fi-kartik-phase-runner-result/0816h/v1",
        "accepted": True,
        "release_qualified": False,
        "production_touched": False,
        "registry_stdout_sha256": registry.stdout_sha256,
        "matrix_stdout_sha256": matrix.stdout_sha256,
        "registry_handoff_sha256": registry_handoff,
        "matrix_cell_count": 108,
        "environment_key_count": ENV_KEY_COUNT,
        "environment_key_sha256": _sha256("\n".join(keys)),
        "confidential_environment_digest_emitted": False,
        "local_image_binding": image_binding,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_contract(args.contract)
    except RunnerViolation as exc:
        print(
            json.dumps(
                {
                    "schema": "fi-kartik-phase-runner-result/0816h/v1",
                    "accepted": False,
                    "reason": str(exc),
                    "release_qualified": False,
                    "production_touched": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "schema": "fi-kartik-phase-runner-result/0816h/v1",
                    "accepted": False,
                    "reason": f"internal runner failure: {type(exc).__name__}",
                    "release_qualified": False,
                    "production_touched": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
