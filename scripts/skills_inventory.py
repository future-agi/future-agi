"""Public, credential-free local compatibility inventory entrypoint."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

# Only the trusted, installed product tree is importable; never an input folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "futureagi"))


def write_packet(path: Path, report: dict, capture: dict) -> dict:
    """One file carrying exactly the inventoried bytes into the browser review.

    It replaces choosing each source again and hand-copying identity JSON. The
    server re-derives every identity and digest; the packet is not trusted.
    """
    canonical = json.dumps(report, ensure_ascii=True, sort_keys=True).encode()
    ordered = [s for s in capture["sources"] if s["kind"] == "langfuse"] + [
        s for s in capture["sources"] if s["kind"] == "folder"
    ]
    sources = []
    for source in ordered:
        if source["kind"] == "langfuse":
            sources.append({"kind": "langfuse", "export": source["export"]})
            continue
        sources.append(
            {
                "kind": "folder",
                # Same relative paths a browser folder upload sends.
                "files": [
                    {
                        "path": f"{source['base']}/{item}",
                        "content_base64": base64.b64encode(raw).decode("ascii"),
                    }
                    for item, raw in source["entries"]
                ],
                "source_identities": source["source_identities"],
            }
        )
    packet = {
        "schema": 1,
        "kind": "futureagi-migration-packet",
        "inventory_sha256": hashlib.sha256(canonical).hexdigest(),
        "source_member_count": report["source_member_count"],
        "context": capture["context"],
        "sources": sources,
    }
    raw = json.dumps(packet, ensure_ascii=True, sort_keys=True).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "sources": len(sources),
        "inventory_sha256": packet["inventory_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory a whole private skill library locally; no credentials, uploads or execution."
    )
    parser.add_argument("--folder", action="append", default=[], type=Path)
    parser.add_argument(
        "--langfuse",
        action="append",
        default=[],
        type=Path,
        help="A bounded langfuse-skills-v1 exchange file (not arbitrary native export)",
    )
    parser.add_argument(
        "--agents",
        required=True,
        type=Path,
        help="Explicit authored AGENTS.md for the isolated trial",
    )
    parser.add_argument("--name", required=True, help="Proposed immutable context name")
    parser.add_argument("--description", required=True, help="Proposed context purpose")
    parser.add_argument(
        "--select",
        action="append",
        help="Explicit member key; repeat to select a reviewed subset",
    )
    parser.add_argument(
        "--source-origin",
        help="Stable local source registry identity, not a URL to fetch",
    )
    parser.add_argument(
        "--source-project", help="Stable project identity for local resources"
    )
    parser.add_argument(
        "--source-revision",
        help="Immutable local source revision; change it when source bytes change",
    )
    parser.add_argument(
        "--packet",
        type=Path,
        help="Also write one private migration packet (the inventoried bytes, "
        "source identities and proposed context) for the retained-migration "
        "review. The file must not exist yet; nothing is uploaded.",
    )
    parser.add_argument(
        "--require-fit",
        action="store_true",
        help="Exit 2 if the selected compiled closure does not fit",
    )
    args = parser.parse_args()
    if not args.folder and not args.langfuse:
        parser.error("Supply at least one --folder or --langfuse input")
    local_source = {
        "origin": args.source_origin,
        "project": args.source_project,
        "revision": args.source_revision,
    }
    if any(local_source.values()) and not all(local_source.values()):
        parser.error("Supply all three local source identity options together")
    try:
        from model_hub.services.skills_hub.r3.local_inventory import inventory

        capture = {} if args.packet else None
        result = inventory(
            folders=args.folder,
            exchanges=args.langfuse,
            agents=args.agents,
            name=args.name,
            description=args.description,
            selected=args.select,
            local_source=local_source if all(local_source.values()) else None,
            capture=capture,
        )
        if capture is not None:
            result["migration_packet"] = write_packet(args.packet, result, capture)
    except ImportError:
        print(
            json.dumps(
                {
                    "complete_inventory": False,
                    "error": "Install the product Python dependencies (including PyYAML), or set SKILLS_INVENTORY_PYTHON to that interpreter.",
                }
            )
        )
        return 1
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # Private names, contents and exception strings never appear in errors.
        print(
            json.dumps(
                {
                    "complete_inventory": False,
                    "error": "Unreadable, changed, oversized or invalid input. No partial inventory or intake was produced.",
                }
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 2 if args.require_fit and not result["compiled_closure"]["fits"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
