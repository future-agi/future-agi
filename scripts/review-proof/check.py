#!/usr/bin/env python3
"""Judge one Playwright JSON report against the proof plan (run with python3 -I).

  e2e_proof_check.py MUTATIONS.json REPORT.json RUN_ID OUT_VERDICT.json [EXPECTED_TEST_COUNT]

G* runs: every test must have passed (default count 7).
P* runs: each test a mutation of RUN_ID targets must fail at the mutation's
fail_line in the mutation's file with every expect_msg substring in its first
error; every other test in the report must pass.
Exit 0 only if the run proves what the plan says it proves.
"""
import json
import re
import sys

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FLOW = re.compile(r"^([A-Z]+-E2E-\d{3}):")


def walk(suite, out):
    for spec in suite.get("specs", []):
        for test in spec.get("tests", []):
            out.append((spec, test))
    for child in suite.get("suites", []):
        walk(child, out)


def first_error(test):
    for result in test.get("results", []):
        errs = result.get("errors") or ([result["error"]] if result.get("error") else [])
        if errs:
            return result, errs
    return None, []


def spec_frame(errs, file_suffix=".spec.ts"):
    """file:line of the first stack frame inside a spec file (the failing expect)."""
    for e in errs:
        for m in re.finditer(r"(\S+" + re.escape(file_suffix) + r"):(\d+):\d+", ANSI.sub("", e.get("stack", "") or "")):
            return m.group(1), int(m.group(2))
    return None, None


def located(result, errs, file_suffix, line):
    locs = [result.get("errorLocation") or {}] + [e.get("location") or {} for e in errs]
    if any(l.get("file", "").endswith(file_suffix) and l.get("line") == line for l in locs):
        return True
    path, got = spec_frame(errs, file_suffix)
    return path is not None and got == line


def main(argv):
    if len(argv) not in (5, 6):
        sys.exit(__doc__)
    muts = json.load(open(argv[1]))["mutations"]
    report = json.load(open(argv[2]))
    run = argv[3]
    want_count = int(argv[5]) if len(argv) == 6 else 7
    tests = []
    for suite in report.get("suites", []):
        walk(suite, tests)
    rows, ok = [], True
    targets = {}
    for m in muts:
        if m["run"] == run:
            for flow in m["flow"].split("+"):
                targets[flow] = m
    if len(tests) != want_count:
        ok = False
        rows.append({"error": f"expected {want_count} tests, report has {len(tests)}"})
    if report.get("errors"):
        ok = False
        rows.append({"error": "Playwright reported a global error"})
    for spec, test in tests:
        if test.get("expectedStatus") != "passed" or len(test.get("results", [])) != 1:
            ok = False
            rows.append({"error": "A test was skipped, expected to fail, or retried"})
        match = FLOW.match(spec["title"])
        flow = match.group(1) if match else spec["title"]
        status = test.get("status")
        result, errs = first_error(test)
        # errors[].message includes a rendered source frame. Match only the
        # original thrown message, never words copied from the mutated line.
        raw = (result or {}).get("error") or {}
        msg = ANSI.sub("", raw.get("message") or raw.get("value") or "")
        loc = (result or {}).get("errorLocation") or {}
        path, line = (loc.get("file"), loc.get("line")) if loc else spec_frame([raw])
        row = {"flow": flow, "status": status,
               "duration_ms": sum(r.get("duration", 0) for r in test.get("results", [])),
               "error_location": f"{path.split('/')[-1]}:{line}" if path else None,
               "error_head": "\n".join(msg.splitlines()[:14])}
        if flow in targets:
            m = targets[flow]
            row["mutation"] = m["id"]
            row["expected_fail_line"] = m["fail_line"]
            good = (status == "unexpected" and located(result or {}, errs, m["file"], m["fail_line"])
                    and all(s in msg for s in m["expect_msg"]))
            row["proves_check"] = good
            ok &= good
        else:
            row["control_passed"] = status == "expected"
            ok &= status == "expected"
        rows.append(row)
    missing = sorted(set(targets) - {r.get("flow") for r in rows})
    if missing:
        ok = False
        rows.append({"error": f"targeted flows missing from report: {missing}"})
    verdict = {"run": run, "ok": ok, "stats": report.get("stats"), "tests": rows}
    with open(argv[4], "w") as f:
        json.dump(verdict, f, indent=2)
    for r in rows:
        tag = "ERROR" if "error" in r else ("PROOF" if "mutation" in r else "CONTROL")
        good = r.get("proves_check", r.get("control_passed"))
        print(f"{tag:7} {r.get('flow', '')!s:12} {r.get('status', '')!s:10} {r.get('error_location') or '-':45} {'ok' if good else 'NOT OK'} {r.get('error', '')}")
    print(f"RUN {run}: {'OK' if ok else 'NOT OK'}  -> {argv[4]}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv)
