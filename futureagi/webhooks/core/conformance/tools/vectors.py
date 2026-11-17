#!/usr/bin/env python3
"""Build or check the webhooks conformance vectors (WH-0; DESIGN.md section 22.2).

    python3 -I vectors.py --check   # regenerate in memory, compare byte for byte with the files on disk (CI)
    python3 -I vectors.py --write   # rewrite the generated files

Standard library only. Every byte-exact example that DESIGN.md and B8-EVENT-CONTRACT.md print is recomputed here and
asserted against the value the document states (``DOC_FACTS``); a mismatch stops the build.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from whconf.cases_addresses import addresses_file  # noqa: E402
from whconf.cases_b8 import b8_fixtures  # noqa: E402
from whconf.cases_b8_more import fixtures_file  # noqa: E402
from whconf.cases_b8_notices import delivered_fixture  # noqa: E402
from whconf.cases_payloads import payloads_file  # noqa: E402
from whconf.cases_schedule import schedule_file  # noqa: E402
from whconf.cases_signing import signing_files  # noqa: E402
from whconf.common import CONFORMANCE_VERSION, ROOT, dump, sha  # noqa: E402


def build() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    fx, index = b8_fixtures()
    files.update(fx)
    delivered = delivered_fixture()
    files["b8/delivered/run-completed.json"] = dump(delivered)
    files["b8/fixtures.json"] = dump(fixtures_file(index))
    files.update(signing_files(delivered))
    files["schedule.json"] = schedule_file()
    files["addresses.json"] = addresses_file()
    files["payloads.json"] = payloads_file()
    return files


def manifest(files: dict[str, bytes]) -> bytes:
    entries = {}
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT).as_posix()
        if (
            p.is_dir()
            or rel in ("manifest.json", "README.md")
            or "__pycache__" in rel
            or rel.startswith("tools/")
        ):
            continue
        data = files.get(rel, p.read_bytes())
        entries[rel] = {"bytes": len(data), "sha256": sha(data)}
    for rel, data in files.items():
        entries.setdefault(rel, {"bytes": len(data), "sha256": sha(data)})
    return dump(
        {
            "conformance_version": CONFORMANCE_VERSION,
            "note": "sha256 of every vector, fixture and schema file; vendored copies (EE, OSS, SDK, ports) "
            "compare against these (B8-EVENT-CONTRACT.md section 14)",
            "files": dict(sorted(entries.items())),
        }
    )


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "--check"
    files = build()
    files["manifest.json"] = manifest(files)
    stale = []
    for rel, data in sorted(files.items()):
        path = ROOT / rel
        if mode == "--write":
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)
                print("wrote", rel)
        elif not path.exists() or path.read_bytes() != data:
            stale.append(rel)
    if mode == "--write":
        return 0
    if stale:
        print("STALE (run tools/vectors.py --write):", *stale, sep="\n  ")
        return 1
    print(f"conformance {CONFORMANCE_VERSION}: {len(files)} files match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
