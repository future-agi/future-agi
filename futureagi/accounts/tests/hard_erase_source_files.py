"""Deterministic offline source discovery, independent of Git and app startup."""

import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
EE = runpy.run_path(str(ROOT / "futureagi/tfc/ee_loader.py"))
# Preserve the source census's sensitive-path exclusions before reading anything.
EXCLUDED_WORDS = ("settings", "secret", ".env", "values")
# Metadata, dependency and interpreter caches are not application source. Do not
# apply arbitrary ignore rules to unknown Python/SQL: new source must be checked.
NON_SOURCE_DIRS = {
    ".git", ".hg", ".svn", ".claude", "__pycache__", "node_modules", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".uv-cache",
    ".tox", ".nox", ".hypothesis", ".ipynb_checkpoints",
}


def excluded(path):
    return any(word in part.lower() for part in path.parts for word in EXCLUDED_WORDS)


def source_files(root):
    """Return sorted relative Python/SQL paths, including unreviewed source.

    Enumeration and read errors are not absence: propagate them. Symlinks in the
    source tree must be reviewed, not silently followed outside the archive or
    omitted. Only known non-source directories and sensitive inputs are pruned.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError("source root is missing")

    def failed(error):
        raise error

    names = []
    for directory, dirs, files in os.walk(root, onerror=failed, followlinks=False):
        parent = Path(directory)
        kept = []
        for name in sorted(dirs):
            path = parent / name
            if name in NON_SOURCE_DIRS or name.startswith(".venv") or excluded(path.relative_to(root)):
                continue
            if path.is_symlink():
                raise ValueError("source census refuses symlink directory")
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = parent / name
            relative = path.relative_to(root)
            if path.suffix not in {".py", ".sql"} or excluded(relative):
                continue
            if path.is_symlink():
                raise ValueError("source census refuses symlink input")
            names.append(relative.as_posix())
    return sorted(names)


def ee_available(root):
    """Use the product presence gate, not existence of an individual EE file.

    The offline suite does not start Django. Add this tree's import root only
    while asking the real has_ee helper about the top-level package: once EE is
    present, a missing selected module remains a hard source-read failure.
    """
    with patch.object(sys, "path", [str(Path(root) / "futureagi"), *sys.path]):
        return EE["has_ee"]("ee")


def available_effect_rows(root, rows):
    present = ee_available(root)
    return {key: row for key, row in rows.items() if present or not key.startswith("ee.")}
