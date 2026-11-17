"""Check the omega-ee-attach/v1 fixtures, the spec that cites them, and the code they copy.

Run it through check.sh. Standard library only, under `python3 -I`; the sibling modules are loaded by
path, because -I keeps the script's directory off sys.path.

1. Fixtures: each entry of fixtures.json parses and is validated against its schema.json $defs entry
   (schema_subset.py), then its named checks run (fixture_checks.py). A fixture under forbidden/ must
   fail for the stated reason: a (JSON pointer, keyword) error, or a named check.
2. Cross-fixture checks (fixtures.json cross_checks), and every tools/call request fixture calls a name a
   tools/list fixture lists.
3. SHA256SUMS and the set digest the spec states (source_checks.sums).
4. Code checks against the repository (source_checks.code_checks).
5. Typed results: the names in the spec's marked table equal the names on MCP-PLAN.md:1179-1180. With
   --plan none (CI, where the plan file is absent) the names come from the two lines vendored in
   typed-results.json, which must hash to the pinned sha256.
6. With --reference-python, the jsonschema package under that interpreter must agree with step 1 on
   every fixture: a check of schema_subset.py, not of the fixtures.

--write-sums re-pins SHA256SUMS after a deliberate change and prints the new set digest; the spec's
"Set digest" line must then be updated by hand.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = (
    True  # the sibling modules below leave no __pycache__ in the fixtures directory
)
HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = "docs/gateway/connectors/omega-ee-attach.md"
FAILURES = []


def load(name):
    spec = importlib.util.spec_from_file_location(
        "omega_ee_attach_" + name, os.path.join(HERE, name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


subset, fx, src = load("schema_subset"), load("fixture_checks"), load("source_checks")


# A fixture or file that lacks the shape a check reads is a failure to report, not a crash.
SHAPE_ERRORS = (OSError, ValueError, KeyError, IndexError, TypeError)


def guarded(fn, *args):
    try:
        return fn(*args)
    except SHAPE_ERRORS as err:
        return f"cannot check it: {type(err).__name__}: {err}"


def report(name, result):
    if result is True:
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {result}")


def run_fixtures(fixdir, manifest, schema):
    validity = {}
    for entry in manifest["fixtures"]:
        name, path = entry["file"], os.path.join(fixdir, entry["file"])
        try:
            doc = src.load_json(path)
        except (OSError, ValueError) as err:
            report(name, f"does not parse: {err}")
            continue
        errors = schema.errors(doc, {"$ref": "#/$defs/" + entry["def"]})
        validity[name] = not errors
        forbidden = name.startswith("forbidden/")
        if forbidden != (entry["expect"] == "invalid"):
            report(name, "forbidden/ holds exactly the fixtures expected to be invalid")
            continue
        if entry["expect"] == "valid":
            # The named checks read the shape the schema guarantees, so they run only on a valid fixture.
            checks = [] if errors else entry.get("checks", [])
            failed = [guarded(fx.CHECKS[c], doc) for c in checks]
            failed = [f for f in failed if f is not True]
            report(
                "{} is a valid {}".format(name, entry["def"]),
                True
                if not errors and not failed
                else "; ".join(["{}: {} {}".format(*e) for e in errors[:5]] + failed),
            )
            continue
        because = entry["because"]
        if "check" in because:
            result = None if errors else guarded(fx.CHECKS[because["check"]], doc)
            good = not errors and result is not True
            report(
                "{} is refused by check {}".format(name, because["check"]),
                True if good else f"schema errors {errors[:3]}, check said {result!r}",
            )
        else:
            hit = any(
                p == because["path"] and k == because["keyword"] for p, k, _ in errors
            )
            report(
                "{} is refused at {} ({})".format(
                    name, because["path"], because["keyword"]
                ),
                True if hit else f"errors were {errors[:6] or 'none: it validated'}",
            )
    return validity


def tools_call_coverage(manifest, load):
    calls = [
        f["file"]
        for f in manifest["fixtures"]
        if f["def"] == "mcp_request"
        and f["expect"] == "valid"
        and load(f["file"])["body"]["method"] == "tools/call"
    ]
    covered = {
        r
        for c in manifest["cross_checks"]
        if c["check"] == "listed_tools_are_called_names"
        for r in c["calls"]
    }
    missing = [f for f in calls if f not in covered]
    return not missing or f"no listed_tools_are_called_names check covers {missing}"


def sessions_follow_one_credential_and_url(manifest, load):
    seen = {}
    for f in manifest["fixtures"]:
        if f["def"] != "mcp_request" or f["expect"] != "valid":
            continue
        http = load(f["file"])["http"]
        session = http["headers"].get("mcp-session-id")
        if session:
            seen.setdefault(session, set()).add(
                (http["url"], http["headers"]["authorization"])
            )
    shared = {s: sorted(p) for s, p in seen.items() if len(p) > 1}
    return (
        not shared
        or f"a session id used with more than one (url, credential): {shared}"
    )


def run_typed_results(plan, typed, spec, schema):
    names = src.doc_table_names(spec)
    if names is None:
        report(
            "the spec has a typed-results table",
            f"no <!-- typed-results:begin/end --> block in {SPEC}",
        )
        return
    dupes = sorted({n for n in names if names.count(n) > 1 or n is None}, key=str)
    report(
        "the typed-results table names each name once",
        True if not dupes else f"repeated or empty: {dupes}",
    )
    pinned = set(typed["plan_names"])
    if plan == "none":
        l1179, l1180 = typed["source"]["text"]
        lines = None
        digest = hashlib.sha256((l1179 + "\n" + l1180).encode()).hexdigest()
        print(
            "note MCP_PLAN=none: comparing with the two plan lines vendored in typed-results.json, "
            "not with the plan file"
        )
        report(
            "the vendored plan lines are the pinned MCP-PLAN.md:1179-1180",
            digest == typed["source"]["sha256"]
            or f"sha256 {digest}, pinned {typed['source']['sha256']}",
        )
    else:
        try:
            l1179, l1180, lines = src.plan_lines(plan)
        except (OSError, IndexError) as err:
            report(
                "MCP-PLAN.md is readable",
                f"{plan}: {getattr(err, 'strerror', None) or 'fewer than 1180 lines'}. Set MCP_PLAN "
                "to its path, or MCP_PLAN=none to compare with the lines vendored in typed-results.json",
            )
            return
        digest = hashlib.sha256((l1179 + "\n" + l1180).encode()).hexdigest()
    source, fields, enums = src.typed_results(l1179, l1180)
    report(
        "MCP-PLAN.md:1179-1180 still name the pinned typed results",
        True
        if source == pinned
        else f"only in the plan {sorted(source - pinned)}; only pinned {sorted(pinned - source)}; "
        + (src.where_moved(lines) if lines else "in the vendored lines"),
    )
    report(
        "billing_mode is byok | managed in the plan and the schema",
        True
        if enums
        == [typed["billing_modes"]]
        == [
            schema.root["$defs"]["tool_call_meta"]["properties"]["billing_mode"]["enum"]
        ]
        else f"plan {enums}",
    )
    if lines and digest != typed["source"]["sha256"]:
        print(
            "note MCP-PLAN.md:1179-1180 changed (sha256 {}, pinned {}) but names the same typed results; "
            "re-pin typed-results.json and its vendored text".format(
                digest, typed["source"]["sha256"]
            )
        )
    doc = {n for n in names if n}
    report(
        f"the spec's typed-results table equals MCP-PLAN.md:1179-1180 ({len(source)} names)",
        True
        if doc == source
        else f"only in the spec {sorted(doc - source)}; only in the plan {sorted(source - doc)}",
    )
    missing = [n for n in typed["required_in_doc"] if n not in doc]
    report(
        "the four typed results and io.futureagi/tool_call are in the table",
        True if not missing else missing,
    )


REFERENCE = r"""
import json, sys
from jsonschema import Draft202012Validator
schema, manifest, base = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), sys.argv[3]
out = {}
for f in manifest["fixtures"]:
    s = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/" + f["def"]}
    Draft202012Validator.check_schema(s)
    out[f["file"]] = Draft202012Validator(s).is_valid(json.load(open(base + "/" + f["file"])))
print(json.dumps(out))
"""


def run_reference(python, fixdir, validity):
    args = [
        python,
        "-I",
        "-c",
        REFERENCE,
        os.path.join(fixdir, "schema.json"),
        os.path.join(fixdir, "fixtures.json"),
        fixdir,
    ]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        report("the jsonschema reference ran", proc.stderr.strip()[-400:])
        return
    try:
        ref = json.loads(proc.stdout)
    except ValueError:
        report(
            "the jsonschema reference ran", f"unreadable output: {proc.stdout[-200:]!r}"
        )
        return
    differ = sorted(f for f in validity if ref.get(f) != validity[f])
    report(
        f"jsonschema (Draft 2020-12) agrees on all {len(validity)} fixtures",
        True if not differ and len(ref) == len(validity) else f"disagree on {differ}",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--reference-python")
    ap.add_argument("--write-sums", action="store_true")
    args = ap.parse_args()
    manifest = src.load_json(os.path.join(HERE, "fixtures.json"))
    if args.write_sums:
        print(f"sha256:{src.write_sums(HERE, manifest)}")
        return 0
    schema = subset.Schema(src.load_json(os.path.join(HERE, manifest["schema"])))
    validity = run_fixtures(HERE, manifest, schema)

    def load_fixture(f):
        return src.load_json(os.path.join(HERE, f))

    for c in manifest["cross_checks"]:
        report(
            "cross check {}".format(c["check"]),
            guarded(fx.cross, c, load_fixture, schema),
        )
    report(
        "every tools/call fixture calls a name a tools/list fixture lists",
        guarded(tools_call_coverage, manifest, load_fixture),
    )
    report(
        "each session id travels with one url and one credential (spec section 4.1)",
        guarded(sessions_follow_one_credential_and_url, manifest, load_fixture),
    )
    try:
        spec = src.read(args.repo, SPEC)
    except OSError as err:
        report("the spec is readable", f"{SPEC}: {err.strerror}")
        spec = ""
    for run in (
        lambda: src.sums(HERE, manifest, spec),
        lambda: src.code_checks(args.repo, schema.root),
    ):
        try:
            results = run()
        except OSError as err:
            report(
                "the pinned and cited files are readable",
                f"{err.filename}: {err.strerror}",
            )
            continue
        for name, result in results:
            report(name, result)
    run_typed_results(
        args.plan, src.load_json(os.path.join(HERE, "typed-results.json")), spec, schema
    )
    if args.reference_python:
        run_reference(args.reference_python, HERE, validity)
    total = len(manifest["fixtures"])
    invalid = sum(1 for f in manifest["fixtures"] if f["expect"] == "invalid")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) failed")
        return 1
    print(
        f"PASS: {total} fixtures ({total - invalid} valid, {invalid} refused for the stated reason), "
        f"{len(manifest['cross_checks'])} cross checks, code and plan checks"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
