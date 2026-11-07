"""Fail if Skills generation changes any pre-existing Swagger/MCP contract."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def baseline(path):
    return json.loads(
        subprocess.check_output(
            ["git", "show", f"origin/dev:{path}"], cwd=ROOT, text=True
        )
    )


def current(path):
    return json.loads((ROOT / path).read_text())


def main():
    path = "api_contracts/openapi/swagger.json"
    before, after = baseline(path), current(path)
    for group in ("paths", "definitions"):
        changed = [
            key for key, value in before[group].items()
            if after[group].get(key) != value
        ]
        assert not changed, f"Changed existing {group}: {changed}"
        added = after[group].keys() - before[group].keys()
        assert all(
            key.startswith("/model-hub/skills/") if group == "paths"
            else key.startswith("Skills")
            for key in added
        ), f"Unexpected added {group}: {sorted(added)}"
        print(f"PASS: {len(before[group])} existing {group} unchanged; {len(added)} Skills additions")
    path = "futureagi/mcp_server/catalog/tools.generated.json"
    before, after = baseline(path), current(path)
    tools = {tool["name"]: tool for tool in after["tools"]}
    assert all(tools[tool["name"]] == tool for tool in before["tools"])
    assert len(after["tools"]) == after["tool_count"] == 113
    print(f"PASS: {len(before['tools'])} existing MCP tools unchanged; 6 Skills tools added")


if __name__ == "__main__":
    main()
