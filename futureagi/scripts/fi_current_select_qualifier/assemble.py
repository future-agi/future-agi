#!/usr/bin/env python3
"""Assemble an immutable, inert launch bundle for the CATALOG qualifier.

The assembler performs no network calls and never launches a container.  It
must write outside the repository so generated artifacts cannot silently alter
the source identity they bind.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from safety import (
    BASE_COMMIT,
    SCHEMA,
    SafetyViolation,
    canonical_json_bytes,
    safe_relative_path,
    sha256_bytes,
)

PACKAGE_DIR = Path(__file__).resolve().parent
QUALIFIER_FILES = ("qualify.py", "safety.py", "apply_runtime_deletions.py")
_IMAGE_DIGEST_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_RUNTIME_REQUIRED = frozenset(
    {
        "accounts/authentication.py",
        "accounts/models/user.py",
        "accounts/models/workspace.py",
        "model_hub/models/develop_dataset.py",
        "model_hub/services/dataset_table_snapshot.py",
        "model_hub/views/develop_dataset.py",
        "simulate/models/run_test.py",
        "simulate/models/test_execution.py",
        "simulate/serializers/preview_pagination.py",
        "simulate/services/preview_pagination.py",
        "simulate/urls.py",
        "simulate/views/preview_pagination.py",
        "tfc/middleware/workspace_context.py",
        "tfc/settings/settings.py",
        "tfc/urls.py",
        "tracer/models/project.py",
        "tracer/serializers/dashboard.py",
        "tracer/serializers/filters.py",
        "tracer/serializers/trace.py",
        "tracer/serializers/trace_session.py",
        "tracer/services/clickhouse/list_cursor.py",
        "tracer/services/clickhouse/read_budget.py",
        "tracer/services/clickhouse/server_readonly.py",
        "tracer/services/clickhouse/v2/query_service.py",
        "tracer/services/users_list_manager.py",
        "tracer/urls.py",
        "tracer/utils/property_registry.py",
        "tracer/utils/workspace_scope.py",
        "tracer/views/dashboard.py",
        "tracer/views/observation_span.py",
        "tracer/views/project.py",
        "tracer/views/trace.py",
        "tracer/views/trace_session.py",
    }
)


def _is_suspicious_source_path(relative: str) -> bool:
    """Identify likely credential payloads without rejecting source modules.

    Names such as ``credentials.py``, ``secrets.go``, ``.secrets.baseline``,
    and ``.env.production.example`` are source or scanner metadata. They must
    not be confused with an actual environment/credential artifact.
    """

    name = PurePosixPath(relative).name.lower()
    if name == ".env":
        return True
    if name.startswith(".env.") and not name.endswith(
        (".example", ".sample", ".template")
    ):
        return True
    if name in {
        "credential",
        "credentials",
        "secret",
        "secrets",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }:
        return True
    if PurePosixPath(name).suffix in {".pem", ".p12", ".pfx", ".key"}:
        return True
    if name.endswith(".json") and re.search(
        r"(?:^|[-_.])(?:credential|credentials|secret|secrets|service[-_]?account)"
        r"(?:[-_.]|$)",
        name,
    ):
        return True
    return False


@dataclass(frozen=True)
class SourceEntry:
    path: str
    kind: str
    mode: int
    size: int
    sha256: str
    link_target: str | None = None

    def payload(self) -> dict[str, object]:
        result: dict[str, object] = {
            "path": self.path,
            "kind": self.kind,
            "mode": self.mode,
            "size": self.size,
            "sha256": self.sha256,
        }
        if self.link_target is not None:
            result["link_target"] = self.link_target
        return result


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _split_z(value: bytes) -> list[str]:
    return [part.decode("utf-8", "strict") for part in value.split(b"\0") if part]


def _repo_files(
    repo: Path,
    *,
    allowed_missing: frozenset[str] = frozenset(),
) -> list[str]:
    paths = _split_z(_git(repo, "ls-files", "-c", "-o", "--exclude-standard", "-z"))
    if len(paths) != len(set(paths)):
        raise SafetyViolation("git source inventory contains duplicate paths")
    present = []
    missing_runtime = []
    for relative in paths:
        if os.path.lexists(repo / PurePosixPath(relative)):
            present.append(relative)
        elif _runtime_path(relative) is not None and relative not in allowed_missing:
            missing_runtime.append(relative)
    if missing_runtime:
        raise SafetyViolation(
            "tracked backend runtime files are absent: "
            + ",".join(missing_runtime[:20])
        )
    return sorted(present)


def _dirty_states(repo: Path) -> dict[str, str]:
    tokens = _split_z(
        _git(
            repo,
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            BASE_COMMIT,
            "--",
        )
    )
    if len(tokens) % 2:
        raise SafetyViolation("git dirty-state output was malformed")
    states = {tokens[index + 1]: tokens[index] for index in range(0, len(tokens), 2)}
    for path in _split_z(
        _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    ):
        states[path] = "A-untracked"
    return dict(sorted(states.items()))


def _read_entry(repo: Path, relative: str) -> tuple[SourceEntry, bytes]:
    if _is_suspicious_source_path(relative):
        raise SafetyViolation(f"refusing suspicious source path: {relative}")
    path = repo / PurePosixPath(relative)
    path_stat = path.lstat()
    mode = stat.S_IMODE(path_stat.st_mode)
    if stat.S_ISLNK(path_stat.st_mode):
        target = os.readlink(path)
        data = target.encode("utf-8")
        return (
            SourceEntry(
                path=relative,
                kind="symlink",
                mode=mode,
                size=len(data),
                sha256=sha256_bytes(data),
                link_target=target,
            ),
            data,
        )
    if not stat.S_ISREG(path_stat.st_mode):
        raise SafetyViolation(
            f"source inventory contains a non-regular file: {relative}"
        )
    data = path.read_bytes()
    return (
        SourceEntry(
            path=relative,
            kind="file",
            mode=mode,
            size=len(data),
            sha256=sha256_bytes(data),
        ),
        data,
    )


def _inventory(
    repo: Path,
    *,
    allowed_missing: frozenset[str] = frozenset(),
) -> tuple[list[SourceEntry], dict[str, bytes]]:
    entries: list[SourceEntry] = []
    content: dict[str, bytes] = {}
    for relative in _repo_files(repo, allowed_missing=allowed_missing):
        entry, data = _read_entry(repo, relative)
        entries.append(entry)
        content[relative] = data
    return entries, content


def _base_regular_file(repo: Path, relative: str) -> bytes:
    """Return one regular file from the pinned Git base for deletion binding."""

    safe_relative_path(relative)
    listing = _git(repo, "ls-tree", "-z", BASE_COMMIT, "--", relative)
    if not listing:
        raise SafetyViolation(f"deleted source was absent from base commit: {relative}")
    metadata, separator, listed_path = listing.rstrip(b"\0").partition(b"\t")
    fields = metadata.split()
    if (
        separator != b"\t"
        or listed_path.decode("utf-8", "strict") != relative
        or len(fields) != 3
        or fields[0] not in {b"100644", b"100755"}
        or fields[1] != b"blob"
    ):
        raise SafetyViolation(
            f"deleted source is not one pinned regular file: {relative}"
        )
    return _git(repo, "show", f"{BASE_COMMIT}:{relative}")


def _deletion_inventory(
    repo: Path,
    dirty: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    deleted_files: dict[str, str] = {}
    runtime_deletions: dict[str, str] = {}
    for relative, state in sorted(dirty.items()):
        if not state.startswith("D"):
            continue
        data = _base_regular_file(repo, relative)
        digest = sha256_bytes(data)
        deleted_files[relative] = digest
        runtime_path = _runtime_path(relative)
        if runtime_path is not None:
            runtime_deletions[runtime_path] = digest
    return deleted_files, runtime_deletions


def _runtime_path(repo_relative: str) -> str | None:
    prefix = "futureagi/"
    return repo_relative[len(prefix) :] if repo_relative.startswith(prefix) else None


def _runtime_overlay(
    entries: list[SourceEntry],
    content: dict[str, bytes],
) -> tuple[dict[str, str], list[tuple[str, bytes, int]]]:
    """Return every current regular backend file and its deterministic overlay.

    The base image is only a dependency/runtime carrier.  Its application
    revision is deliberately not trusted, so clean and dirty source files are
    treated identically here.
    """

    runtime_files: dict[str, str] = {}
    members: list[tuple[str, bytes, int]] = []
    for entry in entries:
        runtime_path = _runtime_path(entry.path)
        if runtime_path is None or entry.kind != "file":
            continue
        runtime_files[runtime_path] = entry.sha256
        members.append((runtime_path, content[entry.path], entry.mode))
    if not runtime_files:
        raise SafetyViolation("current source inventory has no backend runtime files")
    return dict(sorted(runtime_files.items())), members


def _tar_bytes(
    destination: Path,
    members: list[tuple[str, bytes, int]],
) -> None:
    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, data, mode in sorted(members):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, fileobj=_BytesReader(data))


class _BytesReader:
    def __init__(self, value: bytes) -> None:
        self._value = value
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._value) - self._offset
        result = self._value[self._offset : self._offset + size]
        self._offset += len(result)
        return result


def _job_template(
    *,
    source_manifest_sha256: str,
    qualifier_sha256: str,
) -> str:
    # Emit one unresolved Job, not four YAML documents. The invalid DNS
    # placeholder prevents accidental application of the template itself;
    # an operator must materialize and finish one named shard at a time.
    return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: fi-current-select-qualifier-__QUALIFIER_SHARD__
spec:
  activeDeadlineSeconds: 5400
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      enableServiceLinks: false
      containers:
        - name: qualify
          image: __DERIVED_IMAGE_DIGEST__
          imagePullPolicy: IfNotPresent
          command: [\"python\", \"/harness/qualify.py\"]
          envFrom:
            - secretRef:
                name: __READ_ONLY_RUNTIME_SECRET__
          env:
            - name: EXPECTED_IMAGE_DIGEST
              value: __DERIVED_IMAGE_DIGEST__
            - name: EXPECTED_BASE_COMMIT
              value: {BASE_COMMIT}
            - name: EXPECTED_SOURCE_MANIFEST_SHA256
              value: {source_manifest_sha256}
            - name: EXPECTED_QUALIFIER_SHA256
              value: {qualifier_sha256}
            - name: QUALIFIER_SHARD
              value: "__QUALIFIER_SHARD__"
            - name: QUALIFIER_END_UTC
              value: "__QUALIFIER_END_UTC__"
            - name: QUALIFIER_RUN_ID
              value: "__QUALIFIER_RUN_ID__"
            - name: DJANGO_SETTINGS_MODULE
              value: tfc.settings.settings
            - name: DJANGO_CACHE_BACKEND
              value: locmem
            - name: NO_STARTUP_DB_MUTATIONS
              value: \"true\"
            - name: STARTUP_DB_MUTATION_MODE
              value: disabled
            - name: PGOPTIONS
              value: \"-c default_transaction_read_only=on -c statement_timeout=9500\"
            - name: SPAN_ATTRIBUTE_CATALOG_READ_MODE
              value: \"off\"
            - name: SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED
              value: \"false\"
            - name: SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK
              value: \"\"
            - name: SPAN_ATTRIBUTE_CATALOG_DATABASE
              value: \"\"
            - name: SPAN_ATTRIBUTE_CATALOG_CH_DATABASE
              value: \"\"
            - name: PYTHONDONTWRITEBYTECODE
              value: \"1\"
          resources:
            requests:
              cpu: \"2\"
              memory: 4Gi
            limits:
              cpu: \"4\"
              memory: 8Gi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: [\"ALL\"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
            - name: logs
              mountPath: /app/backend/logs
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 512Mi
        - name: logs
          emptyDir:
            sizeLimit: 128Mi
"""


def assemble(*, repo: Path, output: Path, base_image: str) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    output = output.resolve(strict=False)
    if not (repo / ".git").exists():
        raise SafetyViolation("repository root does not contain .git")
    if output == repo or repo in output.parents:
        raise SafetyViolation("generated launch bundle must be outside the repository")
    if output.exists():
        raise SafetyViolation("output path already exists; choose a fresh path")
    if not _IMAGE_DIGEST_RE.fullmatch(base_image):
        raise SafetyViolation("base image must be pinned as name@sha256:<64 hex>")
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    if head != BASE_COMMIT:
        raise SafetyViolation(f"HEAD drifted: expected {BASE_COMMIT}, got {head}")

    status_before = _git(repo, "status", "--porcelain=v1", "-z")
    dirty = _dirty_states(repo)
    deleted_files, runtime_deletions = _deletion_inventory(repo, dirty)
    allowed_missing = frozenset(deleted_files)
    entries, content = _inventory(repo, allowed_missing=allowed_missing)
    entries_again, _ = _inventory(repo, allowed_missing=allowed_missing)
    status_after = _git(repo, "status", "--porcelain=v1", "-z")
    if entries != entries_again or status_before != status_after:
        raise SafetyViolation("working tree changed during deterministic assembly")

    entry_by_path = {entry.path: entry for entry in entries}
    runtime_files, overlay_members = _runtime_overlay(entries, content)
    runtime_required = {
        path: entry_by_path[f"futureagi/{path}"].sha256
        for path in sorted(_RUNTIME_REQUIRED)
        if f"futureagi/{path}" in entry_by_path
    }
    missing_runtime = sorted(_RUNTIME_REQUIRED - set(runtime_required))
    if missing_runtime:
        raise SafetyViolation(
            "required runtime source is absent: " + ",".join(missing_runtime)
        )

    dirty_runtime: dict[str, str] = {}
    for relative, _state in dirty.items():
        if relative in deleted_files:
            continue
        runtime_path = _runtime_path(relative)
        if runtime_path is None:
            continue
        entry = entry_by_path.get(relative)
        if entry is None:
            raise SafetyViolation(f"dirty runtime file is absent: {relative}")
        if entry.kind != "file":
            raise SafetyViolation(f"dirty runtime symlink is not supported: {relative}")
        dirty_runtime[runtime_path] = entry.sha256

    source_manifest = {
        "schema": SCHEMA,
        "base_commit": BASE_COMMIT,
        "base_image": base_image,
        "files": [entry.payload() for entry in entries],
        "deleted_files": deleted_files,
        "dirty": dirty,
        "runtime_files": runtime_files,
        "runtime_deletions": sorted(runtime_deletions),
        "runtime_deletion_base_sha256": runtime_deletions,
        "runtime_required": runtime_required,
        "runtime_dirty": dict(sorted(dirty_runtime.items())),
    }
    source_manifest_bytes = canonical_json_bytes(source_manifest)
    source_manifest_sha256 = sha256_bytes(source_manifest_bytes)
    qualifier_bytes = (PACKAGE_DIR / "qualify.py").read_bytes()
    qualifier_sha256 = sha256_bytes(qualifier_bytes)

    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="fi-assemble-", dir=parent
    ) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        (staging / "source-manifest.json").write_bytes(source_manifest_bytes)
        _tar_bytes(staging / "runtime-overlay.tar", overlay_members)
        harness_members = [
            (
                name,
                (PACKAGE_DIR / name).read_bytes(),
                0o555 if name.endswith(".py") else 0o444,
            )
            for name in QUALIFIER_FILES
        ]
        harness_members.append(("source-manifest.json", source_manifest_bytes, 0o444))
        _tar_bytes(staging / "harness.tar", harness_members)
        dockerfile = (
            f"FROM {base_image}\n"
            "ADD runtime-overlay.tar /app/backend/\n"
            "ADD harness.tar /harness/\n"
            'RUN ["python","/harness/apply_runtime_deletions.py",'
            '"/harness/source-manifest.json","/app/backend"]\n'
            "WORKDIR /app/backend\n"
            'ENTRYPOINT ["python","/harness/qualify.py"]\n'
        )
        (staging / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (staging / "job.yaml.template").write_text(
            _job_template(
                source_manifest_sha256=source_manifest_sha256,
                qualifier_sha256=qualifier_sha256,
            ),
            encoding="utf-8",
        )
        artifact_names = (
            "Dockerfile",
            "harness.tar",
            "job.yaml.template",
            "runtime-overlay.tar",
            "source-manifest.json",
        )
        bundle_manifest = {
            "schema": f"{SCHEMA}/bundle",
            "base_commit": BASE_COMMIT,
            "base_image": base_image,
            "source_manifest_sha256": source_manifest_sha256,
            "qualifier_sha256": qualifier_sha256,
            "dirty_file_count": len(dirty),
            "dirty_runtime_file_count": len(dirty_runtime),
            "deleted_file_count": len(deleted_files),
            "runtime_deletion_count": len(runtime_deletions),
            "runtime_file_count": len(runtime_files),
            "artifacts": {
                name: sha256_bytes((staging / name).read_bytes())
                for name in artifact_names
            },
        }
        (staging / "bundle-manifest.json").write_bytes(
            canonical_json_bytes(bundle_manifest)
        )
        staging.rename(output)
    return bundle_manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--base-image",
        required=True,
        help="Exact base image in name@sha256:<digest> form.",
    )
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="Validate arguments and print intent without reading/writing a bundle.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.print_plan:
        print(
            canonical_json_bytes(
                {
                    "action": "assemble_only",
                    "base_commit": BASE_COMMIT,
                    "network": False,
                    "launch": False,
                    "output": str(args.output),
                }
            ).decode(),
            end="",
        )
        return 0
    result = assemble(repo=args.repo, output=args.output, base_image=args.base_image)
    print(canonical_json_bytes(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
