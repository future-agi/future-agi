"""Public, credential-free local compatibility inventory entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Only the trusted, installed product tree is importable; never an input folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "futureagi"))


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

        result = inventory(
            folders=args.folder,
            exchanges=args.langfuse,
            agents=args.agents,
            name=args.name,
            description=args.description,
            selected=args.select,
            local_source=local_source if all(local_source.values()) else None,
        )
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
