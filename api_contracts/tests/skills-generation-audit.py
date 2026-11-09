"""Fail if Skills generation changes any pre-existing Swagger/MCP contract."""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def comparison_base(ref="origin/dev", *, root=ROOT):
    """Compare this branch, not unrelated work that landed on dev after it forked."""
    return subprocess.check_output(
        ["git", "merge-base", ref, "HEAD"], cwd=root, text=True
    ).strip()


def baseline(path, ref):
    return json.loads(
        subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True)
    )


def current(path):
    return json.loads((ROOT / path).read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/dev", help="PR target ref")
    args = parser.parse_args()
    ref = comparison_base(args.base)
    path = "api_contracts/openapi/swagger.json"
    before, after = baseline(path, ref), current(path)
    for group in ("paths", "definitions"):
        changed = [
            key
            for key, value in before[group].items()
            if after[group].get(key) != value
        ]
        assert not changed, f"Changed existing {group}: {changed}"
        added = after[group].keys() - before[group].keys()
        assert all(
            (
                key.startswith("/model-hub/skills/")
                if group == "paths"
                else key.startswith("Skills")
            )
            for key in added
        ), f"Unexpected added {group}: {sorted(added)}"
        print(
            f"PASS: {len(before[group])} existing {group} unchanged; {len(added)} Skills additions"
        )
    path = "futureagi/mcp_server/catalog/tools.generated.json"
    before, after = baseline(path, ref), current(path)
    tools = {tool["name"]: tool for tool in after["tools"]}
    assert all(tools[tool["name"]] == tool for tool in before["tools"])
    assert len(after["tools"]) == after["tool_count"] == 113
    print(
        f"PASS: {len(before['tools'])} existing MCP tools unchanged; 6 Skills tools added"
    )


if __name__ == "__main__":
    main()
