#!/usr/bin/env python3
"""Generate or verify the curated MCP tool manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "futureagi"
sys.path.insert(0, str(BACKEND_ROOT))

from mcp_server.tool_generation import (  # noqa: E402
    generate_tool_manifest,
    write_tool_manifest,
)

DEFAULT_CONTRACT = REPO_ROOT / "api_contracts/openapi/swagger.json"
DEFAULT_CATALOG = BACKEND_ROOT / "mcp_server/catalog/tools.yaml"
DEFAULT_OUTPUT = BACKEND_ROOT / "mcp_server/catalog/tools.generated.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    manifest = generate_tool_manifest(args.contract, args.catalog)
    rendered = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    if args.check:
        if (
            not args.output.exists()
            or args.output.read_text(encoding="utf-8") != rendered
        ):
            print(
                f"Generated MCP manifest is stale: run {Path(__file__).name}",
                file=sys.stderr,
            )
            return 1
        return 0

    write_tool_manifest(manifest, args.output)
    print(f"Generated {manifest['tool_count']} MCP tools at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
