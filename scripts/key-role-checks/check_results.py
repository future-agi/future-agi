"""Compare JUnit identities, preserving shared failures as explicit carry items."""

import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def load(path):
    root = ET.parse(path).getroot()
    cases = {}
    for case in root.iter("testcase"):
        identity = f"{case.get('classname')}::{case.get('name')}"
        if identity in cases:
            raise ValueError(f"Duplicate test identity: {identity}")
        state = "passed"
        if case.find("error") is not None:
            state = "error"
        elif case.find("failure") is not None:
            state = "failed"
        elif case.find("skipped") is not None:
            state = "skipped"
        cases[identity] = state
    if not cases:
        raise ValueError(f"No test cases in {path}")
    return cases


def compare(base, head):
    missing = sorted(set(base) - set(head))
    regressions = sorted(
        name
        for name, state in head.items()
        if state in ("failed", "error") and base.get(name) not in ("failed", "error")
    )
    new_skips = sorted(
        name
        for name, state in head.items()
        if state == "skipped" and base.get(name) != "skipped"
    )
    shared = sorted(
        name
        for name, state in head.items()
        if state in ("failed", "error") and base.get(name) in ("failed", "error")
    )
    return {
        "missing": missing,
        "regressions": regressions,
        "new_skips": new_skips,
        "shared_failures": shared,
    }


def main(raw, output):
    raw, output = Path(raw), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    outcomes = {
        name: load(raw / f"{name}.xml")
        for name in (
            "red",
            "green",
            "base_accounts",
            "head_accounts",
            "dev_accounts",
            "replay_accounts",
        )
    }
    codes = {name: int((raw / f"{name}.exit").read_text()) for name in outcomes}
    red, green = outcomes["red"], outcomes["green"]
    red_families = (
        "test_bootstrap_role_and_authenticated_identity",
        "test_system_key_changes_require_current_org_admin",
        "test_key_tools_use_current_org_membership",
    )
    red_proven = (
        codes["red"] == 1
        and "error" not in red.values()
        and all(
            any(family in name and state == "failed" for name, state in red.items())
            for family in red_families
        )
    )
    green_proven = (
        codes["green"] == 0
        and set(green) == set(red)
        and set(green.values()) == {"passed"}
    )
    main_pair = compare(outcomes["base_accounts"], outcomes["head_accounts"])
    dev_pair = compare(outcomes["dev_accounts"], outcomes["replay_accounts"])
    blocked = not (red_proven and green_proven)
    for label, pair in (("main", main_pair), ("dev", dev_pair)):
        blocked |= bool(pair["missing"] or pair["regressions"] or pair["new_skips"])
    blocked |= any(code not in (0, 1) for code in codes.values())
    # A new fixture failure must not be accepted as an inherited baseline failure.
    blocked |= any("error" in cases.values() for cases in outcomes.values())
    summary = {
        "status": "BLOCKED"
        if blocked
        else "PASSED_WITH_CARRIES"
        if main_pair["shared_failures"] or dev_pair["shared_failures"]
        else "PASSED",
        "red_assertions_proven": red_proven,
        "focused_green_proven": green_proven,
        "main_pair": main_pair,
        "dev_pair": dev_pair,
        "exit_codes": codes,
        "outcomes": outcomes,
    }
    (output / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    # Keep assertion evidence, but remove randomly minted fixture credential strings.
    for source in raw.glob("*.xml"):
        text = re.sub(r"\b[0-9a-fA-F]{32,64}\b", "[fixture-value]", source.read_text())
        (output / source.name).write_text(text)
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "outcomes"},
            indent=2,
        )
    )
    return int(blocked)


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
