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


# Pin both the test identity and its policy assertion. A call-phase exception
# is a JUnit <failure> too; its phase alone is not red regression evidence.
POLICY_ASSERTIONS = {
    "accounts.tests.test_key_role_scope::test_bootstrap_role_and_authenticated_identity": (
        "assert key.type == expected_type",
        r"(?:AssertionError: )?assert 'system' == 'user'(?:\n|$)",
    ),
    "accounts.tests.test_key_management_scope::test_system_key_changes_require_current_org_admin": (
        "assert response.status_code == (200 if permitted else 400)",
        r"(?:AssertionError: )?assert (?:200 == 400|400 == 200)(?:\n|$)",
    ),
    "accounts.tests.test_key_tool_scope::test_key_tools_use_current_org_membership": (
        "assert result.is_error is (level < Level.ADMIN)",
        r"(?:AssertionError: )?assert False is \([13] < 8\)(?:\n|$)",
    ),
}


def policy_assertion_failures(path, assertions=POLICY_ASSERTIONS):
    matched = {family: [] for family in assertions}
    for case in ET.parse(path).getroot().iter("testcase"):
        identity = f"{case.get('classname')}::{case.get('name')}"
        family = identity.split("[", 1)[0]
        failure = case.find("failure")
        if family not in assertions or failure is None:
            continue
        source, message_pattern = assertions[family]
        failure_type = failure.get("type", "")
        if failure_type not in ("", "AssertionError", "builtins.AssertionError"):
            continue
        if not re.match(message_pattern, failure.get("message", "")):
            continue
        # Pytest short and long tracebacks both retain the failing source line.
        lines = {
            line.strip().removeprefix(">").strip()
            for line in (failure.text or "").splitlines()
        }
        if source in lines:
            matched[family].append(identity)
    return matched


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
    policy_failures = policy_assertion_failures(raw / "red.xml")
    red_proven = (
        codes["red"] == 1
        and "error" not in red.values()
        and all(policy_failures.values())
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
    removal_proof = None
    if (raw / "removal_red.xml").exists():
        removal_red = load(raw / "removal_red.xml")
        removal_green = load(raw / "removal_green.xml")
        assertions = {
            "accounts.tests.test_membership_key_healing::test_removed_membership_is_not_healed_by_existing_bearer": (
                "assert client.get(KEYS_URL).status_code == 403",
                r"(?:AssertionError: )?assert 200 == 403(?:\n|$)",
            ),
        }
        removal_failures = policy_assertion_failures(
            raw / "removal_red.xml", assertions
        )
        removal_proof = {
            "red_assertions": removal_failures,
            "red": removal_red,
            "green": removal_green,
            "driver_red_exit": int((raw / "driver-removal.exit").read_text()),
            "driver_green_exit": int((raw / "driver-head.exit").read_text()),
        }
        removal_ok = (
            int((raw / "removal_red.exit").read_text()) == 1
            and int((raw / "removal_green.exit").read_text()) == 0
            and "error" not in removal_red.values()
            and all(removal_failures.values())
            and set(removal_red) == set(removal_green)
            and set(removal_green.values()) == {"passed"}
            and removal_proof["driver_red_exit"] == 1
            and removal_proof["driver_green_exit"] == 0
        )
        blocked |= not removal_ok
    history_proof = None
    if (raw / "history_red.xml").exists():
        history_red = load(raw / "history_red.xml")
        history_green = load(raw / "history_green.xml")
        assertions = {
            "accounts.tests.test_membership_role_history::test_removed_owner_has_no_role_or_private_workspace": (
                "assert str(private.pk) not in {",
                r"(?:AssertionError: )?assert '[^']+' not in ",
            ),
            "accounts.tests.test_membership_role_history::test_resolved_no_organization_does_not_fall_back_to_home_org": (
                "assert resolve_org(SimpleNamespace(user=user, organization=None)) is None",
                r"(?:AssertionError: )?assert <Organization: [^>]+> is None",
            ),
            "accounts.tests.test_system_key_principal::test_system_key_selects_owner_membership_not_first_home_user": (
                'assert response.data["id"] == str(owner.pk)',
                r"(?:AssertionError: )?assert '[^']+' == '[^']+'",
            ),
            "accounts.tests.test_key_management_scope::test_system_key_changes_require_current_org_admin": (
                "assert response.status_code == (200 if permitted else 400)",
                r"(?:AssertionError: )?assert 200 == 400(?:\n|$)",
            ),
        }
        matched = policy_assertion_failures(raw / "history_red.xml", assertions)
        controls = [state for name, state in history_red.items()
                    if "test_workspace_add_does_not_readmit_removed_org_member" in name]
        history_proof = {"red_assertions": matched, "red": history_red, "green": history_green,
                         "workspace_add_controls": controls}
        blocked |= not (
            int((raw / "history_red.exit").read_text()) == 1
            and int((raw / "history_green.exit").read_text()) == 0
            and "error" not in history_red.values()
            and all(matched.values())
            and set(history_red) == set(history_green)
            and set(history_green.values()) == {"passed"}
            and controls == ["passed", "passed"]
        )
    summary = {
        "status": "BLOCKED"
        if blocked
        else "PASSED_WITH_CARRIES"
        if main_pair["shared_failures"] or dev_pair["shared_failures"]
        else "PASSED",
        "red_assertions_proven": red_proven,
        "policy_assertion_failures": policy_failures,
        "focused_green_proven": green_proven,
        "removal_proof": removal_proof,
        "history_proof": history_proof,
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
