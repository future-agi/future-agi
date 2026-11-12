"""Seed disclosures for the Official entries (SPEC §5.4, §14.1, decision 20).

`seeds/official-disclosures.json` holds a draft disclosure for each Official entry, written by hand
from the entry's code. `omega_hub_import_official --fill-missing` puts it in a manifest that has no
`disclosure` of its own; the approving maintainer still confirms or rewrites it in review, like any
other draft. A draft is used only while the entry's code is the code it was written from: each one
carries `codeSha256`, a fingerprint of every file the disclosure describes, and a changed entry is
refused until its text is checked again. There is no placeholder: an entry without a seed is refused.

Pure Python with no Django import. To see which seeds still match an exported Omega tree, and the
fingerprint to record after re-checking a text:

    python3 futureagi/omega_hub/official_seed.py <exported omega dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path

SEED_FILE = Path(__file__).resolve().parent / "seeds" / "official-disclosures.json"
# Files that describe or record an entry but do not change what it does when it runs.
NOT_BEHAVIOUR = frozenset({"omega.hub.json", "README.md", "demo.cast"})
SKIP_DIRS = frozenset({"node_modules", ".git", "dist"})


class SeedError(Exception):
    """An entry has no seed disclosure, or its code changed since the seed was written."""


def _files(root: Path, prefix: str, skip_names: frozenset[str]) -> list[tuple[str, bytes]]:
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or path.is_symlink() or not path.is_file():
            continue
        if not prefix and rel.as_posix() in skip_names:
            continue
        out.append((f"{prefix}{rel.as_posix()}", path.read_bytes()))
    return out


def behaviour_files(entry_dir: Path, manifest: dict, omega_dir: Path) -> list[tuple[str, bytes]]:
    """The entry's files minus NOT_BEHAVIOUR, plus its R11 `source` folder as `source/<path>`."""
    files = _files(entry_dir, "", NOT_BEHAVIOUR)
    source = manifest.get("source")
    if isinstance(source, str) and (omega_dir / source).is_dir():
        files += _files(omega_dir / source, "source/", frozenset())
    return files


def fingerprint(entry_dir: Path, manifest: dict, omega_dir: Path) -> str:
    lines = sorted(f"{rel}\t{hashlib.sha256(data).hexdigest()}" for rel, data in behaviour_files(entry_dir, manifest, omega_dir))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


def disclosure_for(entry_id: str, code_sha256: str) -> dict:
    """The seed draft for `entry_id`, if it was written from this exact code. Raises SeedError."""
    from .disclosure_shape import shape_errors

    seed = load()
    row = seed["entries"].get(entry_id)
    if row is None:
        raise SeedError(f"{entry_id}: no seed disclosure in {SEED_FILE.name}; write one from the entry's code")
    if row["codeSha256"] != code_sha256:
        raise SeedError(
            f"{entry_id}: the code changed since its seed disclosure was written (at {seed['writtenFrom']['commit'][:9]}); "
            f"read the code again, then update the text and codeSha256 in {SEED_FILE.name} "
            f"(python3 futureagi/omega_hub/official_seed.py <omega dir> prints the new fingerprint)"
        )
    errors = shape_errors(row.get("disclosure"))
    if errors:
        raise SeedError(f"{entry_id}: invalid seed disclosure: {'; '.join(errors)}")
    return dict(row["disclosure"])


def report(omega_dir: Path) -> dict[str, dict]:
    """For each `hub/<id>` of an exported tree: its fingerprint, and whether the seed matches it."""
    entries = load()["entries"]
    out = {}
    for entry_dir in sorted(p for p in (omega_dir / "hub").iterdir() if (p / "omega.hub.json").is_file()):
        manifest = json.loads((entry_dir / "omega.hub.json").read_text(encoding="utf-8"))
        entry_id = str(manifest.get("id"))
        code = fingerprint(entry_dir, manifest, omega_dir)
        row = entries.get(entry_id)
        out[entry_id] = {"codeSha256": code,
                         "seed": "missing" if row is None else "match" if row["codeSha256"] == code else "stale"}
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 official_seed.py <exported omega dir>")
    print(json.dumps(report(Path(sys.argv[1]).resolve()), indent=2))
