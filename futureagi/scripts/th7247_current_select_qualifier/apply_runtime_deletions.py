#!/usr/bin/env python3
"""Apply manifest-bound backend deletions while building a qualifier image.

The current-source overlay can replace and add files, but a tar overlay cannot
represent a tracked deletion.  This build-only helper removes exactly the
regular files named by ``runtime_deletions`` after verifying that any file
present in the digest-pinned base image still has the Git-base content recorded
in the source manifest.  Missing targets are already in the desired state.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from safety import SafetyViolation, safe_relative_path, sha256_bytes


def _deletion_map(manifest: Any) -> dict[str, str]:
    if not isinstance(manifest, dict):
        raise SafetyViolation("source manifest is not an object")
    raw_paths = manifest.get("runtime_deletions")
    raw_hashes = manifest.get("runtime_deletion_base_sha256")
    if not isinstance(raw_paths, list) or not all(
        isinstance(value, str) for value in raw_paths
    ):
        raise SafetyViolation("source manifest runtime_deletions list is invalid")
    paths = [safe_relative_path(value).as_posix() for value in raw_paths]
    if paths != sorted(set(paths)):
        raise SafetyViolation("runtime_deletions must be sorted and unique")
    if not isinstance(raw_hashes, dict) or set(raw_hashes) != set(paths):
        raise SafetyViolation("runtime deletion base-hash map is not exact")
    result: dict[str, str] = {}
    for relative, expected_sha256 in raw_hashes.items():
        normalized = safe_relative_path(str(relative)).as_posix()
        expected = str(expected_sha256)
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise SafetyViolation("runtime deletion hash is not a lowercase SHA-256")
        result[normalized] = expected
    return dict(sorted(result.items()))


def _physical_target(root: Path, relative: str) -> Path | None:
    """Return an existing physical target without following a symlink parent."""

    current = root
    parts = safe_relative_path(relative).parts
    for part in parts[:-1]:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise SafetyViolation("runtime deletion parent is not a physical directory")
    target = current / parts[-1]
    try:
        mode = os.lstat(target).st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        raise SafetyViolation("runtime deletion target is not a physical regular file")
    return target


def apply_runtime_deletions(*, manifest_path: Path, backend_root: Path) -> int:
    if not backend_root.is_dir() or backend_root.is_symlink():
        raise SafetyViolation("backend root is not a physical directory")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    deletions = _deletion_map(manifest)
    removed = 0
    for relative, expected_sha256 in deletions.items():
        target = _physical_target(backend_root, relative)
        if target is None:
            continue
        if sha256_bytes(target.read_bytes()) != expected_sha256:
            raise SafetyViolation("base-image runtime deletion content drifted")
        target.unlink()
        if os.path.lexists(target):
            raise SafetyViolation("runtime deletion target remained present")
        removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        raise SystemExit(
            "usage: apply_runtime_deletions.py SOURCE_MANIFEST BACKEND_ROOT"
        )
    apply_runtime_deletions(
        manifest_path=Path(args[0]),
        backend_root=Path(args[1]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
