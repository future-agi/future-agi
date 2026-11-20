"""Route by discovered test identity; never upload raw browser JSON or private attachments."""

import argparse
import json
import re
import sys
from pathlib import Path

MANIFEST = Path(__file__).with_name("skills-coverage.json")
FLOW = re.compile(r"^(SKILLS-E2E-\d+|OBS-E2E-06[34]):")


def owners():
    result = {}
    for cohort, entries in json.loads(MANIFEST.read_text()).items():
        for entry in entries:
            if entry in result:
                raise ValueError(f"Duplicate owner: {entry}")
            result[entry] = cohort
    return result


def specs(suites):
    for suite in suites:
        yield from suite.get("specs", [])
        yield from specs(suite.get("suites", []))


def identity(spec):
    match = FLOW.match(spec["title"])
    if match:
        return match[1]
    return f"{spec['file']} › {spec['title']}"


def pattern(inventory, cohort):
    # Playwright --grep matches the composed file/describe/test/tags string.
    # Leave its existing quarantine/live grepInvert intact, including on ordinary CI.
    expected = owners()
    seen = set()
    patterns = []
    for spec in specs(inventory["suites"]):
        key = identity(spec)
        if key not in expected:
            if FLOW.match(spec["title"]):
                raise ValueError(f"No owner: {key}")
            continue
        if key in seen:
            raise ValueError(f"Duplicate identity: {key}")
        seen.add(key)
        if cohort == "ordinary" or expected[key] == cohort:
            if FLOW.match(spec["title"]):
                patterns.append(r"\b" + key + ":")
            else:
                patterns.append(re.escape(spec["file"]) + r" .*" + re.escape(spec["title"]) + r"(?: |$)")
    if seen != set(expected):
        raise ValueError(f"Absent from discovery: {sorted(set(expected) - seen)}")
    if not patterns:
        raise ValueError(f"Empty selection: {cohort}")
    group = "(?:" + "|".join(patterns) + ")"
    return "^(?!.*" + group + ")" if cohort == "ordinary" else group


def passed(report, cohort):
    expected = {key for key, owner in owners().items() if owner == cohort}
    if not expected:
        raise ValueError(f"Unknown cohort: {cohort}")
    seen = set()
    for spec in specs(report["suites"]):
        key = identity(spec)
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate test in {cohort}")
        seen.add(key)
        tests = spec["tests"]
        if len(tests) != 1:
            raise ValueError(f"Expected one execution: {key}")
        test = tests[0]
        attempts = [(r["retry"], r["status"]) for r in test["results"]]
        if test["expectedStatus"] != "passed" or test["status"] != "expected" or attempts != [(0, "passed")]:
            raise ValueError(f"Did not pass exactly once: {key}")
    if report.get("errors") or seen != expected:
        raise ValueError(f"Incomplete {cohort} report. Missing: {sorted(expected - seen)}")
    return sorted(seen)


# These cases are executed on registered GCP workers, not a hosted runner
# claiming a GCP environment. Their reporter checks the exact Playwright test,
# expected status and single attempt before emitting its allowlisted result.
def registered_passes(directory, commit, sdk, application_sha, expected):
    cases = {
        "056": ("SKILLS-E2E-056", {"campaign": "registered"}),
        "057": ("SKILLS-E2E-057", {"campaign": "registered"}),
        "058": ("SKILLS-E2E-058", {"campaign": "registered"}),
        "059": ("SKILLS-E2E-059", {"campaign": "registered"}),
        "063": ("SKILLS-E2E-063", {"guest": "registered"}),
    }
    for name in ("j3", "j4"):
        (identity_key,) = [key for key in expected if key.startswith(f"harness/skills-r3/{name}-driver")]
        cases[name] = (identity_key, {"fixture": "fixture"} if name == "j3" else
                       {"cold": "completed", "configured": "completed"})
    seen = set()
    for path in directory.rglob("*.json"):
        data = json.loads(path.read_text())
        case = data["case"]
        if case not in cases or path.stem != case or path.parent.name != f"skills-gcp-{case}":
            raise ValueError("Unexpected registered result")
        key, statuses = cases[case]
        records = data["records"]
        if (key in seen or data["sha"] != commit or data["passed"] is not True or data["count"] != 1
                or data["environment"] != "gcp" or data["project"] != "fagi-omega-staging"
                or len(records) != len(statuses) or {r["name"]: r["status"] for r in records} != statuses
                or not all(r["product_head"] == commit and r["sdk_commit"] == sdk for r in records)):
            raise ValueError(f"Invalid or incomplete registered result: {key}")
        if case == "063" and application_sha not in records[0]["package_hashes"]:
            raise ValueError("J6 guest did not use the application wheel from the producer")
        seen.add(key)
    if seen != {key for key, _ in cases.values()}:
        raise ValueError(f"Missing registered passes: {sorted({key for key, _ in cases.values()} - seen)}")
    return seen


def reconcile(directory, commit, registered=None, sdk=None, application_sha=None):
    expected = owners()
    seen = registered_passes(registered, commit, sdk, application_sha, expected) if registered is not None else set()
    cohorts = set(expected.values())
    received = set()
    for path in directory.rglob("*.json"):
        receipt = json.loads(path.read_text())
        if path.stem not in cohorts or path.stem in received:
            raise ValueError("Unknown or duplicate execution receipt")
        received.add(path.stem)
        if receipt["commit"] != commit or path.stem != receipt["cohort"]:
            raise ValueError("Wrong checkout or cohort in execution receipt")
        for key in receipt["passed"]:
            if key in seen or expected.get(key) != path.stem:
                raise ValueError(f"Duplicated or misrouted result: {key}")
            seen.add(key)
    if seen != set(expected):
        raise ValueError(f"Required passes missing: {sorted(set(expected) - seen)}")
    print(f"{len(seen)} required Skills cases passed on {commit}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ["pattern", "receipt"]:
        p = sub.add_parser(cmd)
        p.add_argument("report", type=Path)
        p.add_argument("cohort")
        if cmd == "receipt":
            p.add_argument("commit")
            p.add_argument("out", type=Path)
    p = sub.add_parser("reconcile")
    p.add_argument("directory", type=Path)
    p.add_argument("commit")
    p.add_argument("--registered", type=Path, required=True)
    p.add_argument("--sdk", required=True)
    p.add_argument("--application-sha", required=True)
    args = parser.parse_args()
    try:
        if args.cmd == "pattern":
            print(pattern(json.loads(args.report.read_text()), args.cohort))
        elif args.cmd == "receipt":
            keys = passed(json.loads(args.report.read_text()), args.cohort)
            args.out.write_text(json.dumps(dict(commit=args.commit, cohort=args.cohort, passed=keys)) + "\n")
        else:
            reconcile(args.directory, args.commit, args.registered, args.sdk, args.application_sha)
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(str(exc))
