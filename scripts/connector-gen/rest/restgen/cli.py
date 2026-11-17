"""generate.py: write or check every rest provider's tools.generated.json.

Exit codes: 0 all written or up to date; 1 --check found drift; 2 an
overlay, provider record or spec has a problem (each is printed)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .catalog import GENERATED, current, generate, orphans, overlays, render
from .problems import GenError

DEFAULT_CATALOG = Path(__file__).resolve().parents[4] / "agentcc-gateway" / "catalog" / "connectors"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generate.py",
        description="Generate each rest provider's tools.generated.json from its vendor spec and tools.yaml.",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="the catalog directory (default: the repo's)")
    parser.add_argument("--app", action="append", default=[], help="only this app (repeatable)")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when an output is stale")
    args = parser.parse_args(argv)
    out, err = sys.stdout, sys.stderr
    catalog: Path = args.catalog
    if not (catalog / "apps").is_dir():
        print(f"generate.py: {catalog} has no apps/ directory", file=err)
        return 2
    found = overlays(catalog, args.app or None)
    missing = sorted(set(args.app) - {p.parents[2].name for p in found})
    if missing:
        print(f"generate.py: no rest overlay (apps/<app>/providers/<provider>/tools.yaml) for {', '.join(missing)}", file=err)
        return 2
    problems: list[str] = []
    stale: list[str] = []
    for orphan in orphans(catalog):
        if not args.app or orphan.parents[2].name in args.app:
            rel = orphan.relative_to(catalog).as_posix()
            problems.append(f"{rel}: has no tools.yaml beside it, so nothing can regenerate it")
    for overlay_path in found:
        target = overlay_path.parent / GENERATED
        rel = target.relative_to(catalog).as_posix()
        try:
            doc = generate(overlay_path, catalog)
        except GenError as exc:
            problems += exc.problems
            continue
        if current(target) == doc:
            print(f"ok       {rel} ({len(doc['tools'])} tools)", file=out)
        elif args.check:
            stale.append(rel)
            print(f"stale    {rel}: differs from what tools.yaml and the pinned spec generate; run generate.py", file=out)
        else:
            target.write_text(render(doc), encoding="utf-8")
            print(f"wrote    {rel} ({len(doc['tools'])} tools)", file=out)
    for p in problems:
        print(p, file=err)
    if problems:
        return 2
    if not found:
        print("generate.py: no rest overlays in the catalog; nothing to generate", file=out)
    return 1 if stale else 0
