"""Route by discovered test identity; never upload raw browser JSON or private attachments."""

import argparse
import json
import os
import re
import runpy
import sys
from pathlib import Path

MANIFEST = Path(__file__).with_name("skills-coverage.json")
FLOW = re.compile(r"^(SKILLS-E2E-\d+|OBS-E2E-06[34]):")


def required_runs():
    # A discovered spec may need to pass on multiple topologies.
    result = set()
    for cohort, entries in json.loads(MANIFEST.read_text()).items():
        for entry in entries:
            run = (cohort, entry)
            if run in result:
                raise ValueError(f"Duplicate required run: {run}")
            result.add(run)
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


def test_pattern(key):
    if ' › ' not in key:
        return r"\b" + re.escape(key) + ":"
    filename, title = key.split(' › ', 1)
    return re.escape(filename) + r" .*" + re.escape(title) + r"(?: |$)"


def pattern(inventory, cohort):
    # Playwright --grep matches the composed file/describe/test/tags string.
    # Leave its existing quarantine/live grepInvert intact, including on ordinary CI.
    runs = required_runs()
    expected = {key for _, key in runs}
    if cohort == 'ordinary':
        return '^(?!.*(?:' + '|'.join(test_pattern(key) for key in sorted(expected)) + '))'
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
        if (cohort, key) in runs:
            patterns.append(test_pattern(key))
    if seen != expected:
        raise ValueError(f"Absent from discovery: {sorted(expected - seen)}")
    if not patterns:
        raise ValueError(f"Empty selection: {cohort}")
    return "(?:" + "|".join(patterns) + ")"


def passed(report, cohort):
    expected = {key for owner, key in required_runs() if owner == cohort}
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


ERROR_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:Error|Exception)\b")
# The first line of a Playwright locator/expect failure names the action and the
# locator from the (public) spec; expected/received values follow on later lines.
PLAYWRIGHT_LINE = re.compile(r"^(?:[A-Za-z]*Error: )?(?:locator\.|page\.|expect\(|Timed out \d+ms waiting for expect)")
TOKENISH = re.compile(r"[A-Za-z0-9_\-]{24,}|eyJ[A-Za-z0-9_\-.]+|(?:sk|pk|fi)[-_][A-Za-z0-9_\-]{8,}")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def safe_detail(message):
    first = ANSI.sub("", str(message)).strip().splitlines()[0] if str(message).strip() else ""
    if not PLAYWRIGHT_LINE.match(first):
        return ""
    return TOKENISH.sub("[redacted]", first)[:200]


def failure_summary(report):
    """Allowlisted outcome of every Skills test: identity, status, error type and location.

    Error messages, stdout/stderr and attachments can carry request details, so
    only the error's class name and its spec file:line are emitted.
    """
    lines = []
    for spec in specs(report.get("suites", [])):
        for test in spec.get("tests", []):
            status = test.get("status", "unknown")
            results = test.get("results", [])
            last = results[-1] if results else {}
            where, kind = "", ""
            for error in last.get("errors", []) or ([last["error"]] if last.get("error") else []):
                loc = error.get("location") or {}
                if loc.get("file"):
                    where = f"{Path(loc['file']).name}:{loc.get('line', '?')}"
                found = ERROR_NAME.search(str(error.get("message", ""))[:120])
                kind = found[0] if found else "Error"
                detail = safe_detail(error.get("message", ""))
                if detail:
                    kind = f"{kind} [{detail}]"
                break
            lines.append(f"{identity(spec)} | {status} | {last.get('status', 'not run')} | {kind} {where}".rstrip())
    for error in report.get("errors", []):
        loc = error.get("location") or {}
        lines.append(f"(suite) | error | {Path(loc.get('file', '?')).name}:{loc.get('line', '?')}")
    return lines


# These cases are executed on registered GCP workers, not a hosted runner
# claiming a GCP environment. Their reporter checks the exact Playwright test,
# expected status and single attempt before emitting its allowlisted result.
JOURNEY_RUNNER = "blacksmith-8vcpu-ubuntu-2404"


def registered_passes(directory, commit, sdk, pair_sha, sdk_sha, application_sha, expected, run_id):
    if not re.fullmatch(r"\d+", str(run_id or "")):
        raise ValueError("The Actions run that produced the registered results is required")
    if not all(re.fullmatch(r"[a-f0-9]{64}", value) for value in (pair_sha, sdk_sha, application_sha)):
        raise ValueError("Invalid producer digest")
    cases = {case: ((f"campaign-registered-{case}", f"SKILLS-E2E-{case}"), {"campaign": "registered"})
             for case in ("056", "057", "058", "059")}
    cases["063"] = (("registered-recovery", "SKILLS-E2E-063"), {"guest": "registered"})
    for name in ("j3", "j4"):
        (run,) = [run for run in expected if run[0] == "private-harnesses"
                  and run[1].startswith(f"harness/skills-r3/{name}-driver")]
        cases[name] = (run, {"configured": "completed"} if name == "j3" else
                       {"cold": "completed", "configured": "completed"})
    seen = set()
    for path in directory.rglob("*.json"):
        data = json.loads(path.read_text())
        case = data["case"]
        if case not in cases or path.stem != case or path.parent.name != f"skills-journey-{case}":
            raise ValueError("Unexpected registered result")
        key, statuses = cases[case]
        records = data["records"]
        if (key in seen or data["sha"] != commit or data["passed"] is not True or data["count"] != 1
                or data["pair_sha256"] != pair_sha or data["environment"] != "blacksmith"
                or data.get("runner") != JOURNEY_RUNNER or data.get("run_id") != run_id
                or not all(r.get("environment") == "blacksmith" and r.get("runner") == JOURNEY_RUNNER
                           and r.get("run_id") == run_id for r in records)
                or len(records) != len(statuses) or {r["name"]: r["status"] for r in records} != statuses
                or not all(r["product_head"] == commit and r["sdk_commit"] == sdk for r in records)):
            raise ValueError(f"Invalid or incomplete registered result: {key}")
        # J4 drives the browser/backend; its registration hashes the application manifests, not a Python client.
        if case != "j4":
            wheels = [sdk_sha, application_sha] if case == "063" else [sdk_sha]
            if any(sorted(r["package_hashes"]) != sorted(wheels) for r in records):
                raise ValueError(f"Wrong wheel digests for {case}")
        seen.add(key)
    if seen != {key for key, _ in cases.values()}:
        raise ValueError(f"Missing registered passes: {sorted({key for key, _ in cases.values()} - seen)}")
    return seen


def reconcile(directory, commit, registered=None, sdk=None, pair_sha=None, sdk_sha=None, application_sha=None, run_id=None):
    expected = required_runs()
    seen = (registered_passes(registered, commit, sdk, pair_sha, sdk_sha, application_sha, expected, run_id)
            if registered is not None else set())
    cohorts = {cohort for cohort, _ in expected}
    received = set()
    for path in directory.rglob("*.json"):
        receipt = json.loads(path.read_text())
        if path.stem not in cohorts or path.stem in received:
            raise ValueError("Unknown or duplicate execution receipt")
        received.add(path.stem)
        if receipt["commit"] != commit or path.stem != receipt["cohort"]:
            raise ValueError("Wrong checkout or cohort in execution receipt")
        if pair_sha is not None and path.stem != 'campaign-executor':
            if receipt.get('pair_sha256') != pair_sha:
                raise ValueError(f"Wrong package pair: {path.stem}")
            if path.stem != 'typescript' and receipt.get('sdk_sha256') != sdk_sha:
                raise ValueError(f"Wrong SDK wheel: {path.stem}")
            if path.stem == 'application' and receipt.get('application_sha256') != application_sha:
                raise ValueError("Wrong application wheel")
        for key in receipt["passed"]:
            run = (path.stem, key)
            if run in seen or run not in expected:
                raise ValueError(f"Duplicated or misrouted result: {run}")
            seen.add(run)
    if seen != expected:
        raise ValueError(f"Required passes missing: {sorted(expected - seen)}")
    print(f"{len(seen)} required Skills executions passed on {commit}")


def reconcile_verified(directory, registered, pair, commit, sdk, traceai, pair_sha, sdk_sha, application_sha, run_id=None):
    verifier = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'stack/verify-skill-packages.py'))
    artifacts = verifier['verify'](pair, pair_sha, {'product': commit, 'sdk': sdk, 'traceai': traceai})
    verifier['verify_sums'](pair, artifacts)
    wheel = artifacts['sdk_python']['sha256']
    if wheel != sdk_sha:
        raise ValueError('Verified SDK wheel differs from the independent producer digest')
    reconcile(directory, commit, registered, sdk, pair_sha, wheel, application_sha, run_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ordinary")
    p = sub.add_parser("summary")
    p.add_argument("report", type=Path)
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
    p.add_argument("--traceai", required=True)
    p.add_argument("--pair", type=Path, required=True)
    p.add_argument("--pair-sha", required=True)
    p.add_argument("--sdk-sha", required=True)
    p.add_argument("--application-sha", required=True)
    p.add_argument("--run-id", required=True, help="Actions run whose Blacksmith journeys produced the results")
    args = parser.parse_args()
    try:
        if args.cmd == "summary":
            print("\n".join(failure_summary(json.loads(args.report.read_text()))))
        elif args.cmd == "ordinary":
            print(pattern(None, "ordinary"))
        elif args.cmd == "pattern":
            print(pattern(json.loads(args.report.read_text()), args.cohort))
        elif args.cmd == "receipt":
            keys = passed(json.loads(args.report.read_text()), args.cohort)
            receipt = dict(commit=args.commit, cohort=args.cohort, passed=keys)
            if args.cohort != 'campaign-executor':
                receipt['pair_sha256'] = os.environ['SKILL_TRACE_PAIR_SHA256']
                if args.cohort != 'typescript':
                    pair = json.loads((Path(os.environ['RUNNER_TEMP']) / 'skill-pair/pair.json').read_text())
                    receipt['sdk_sha256'] = pair['artifacts']['sdk_python']['sha256']
                if args.cohort == 'application':
                    receipt['application_sha256'] = os.environ['J6_APP_SHA256']
            args.out.write_text(json.dumps(receipt) + "\n")
        else:
            reconcile_verified(args.directory, args.registered, args.pair, args.commit, args.sdk, args.traceai,
                               args.pair_sha, args.sdk_sha, args.application_sha, args.run_id)
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(str(exc))
