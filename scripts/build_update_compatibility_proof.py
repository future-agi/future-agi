#!/usr/bin/env python3
"""Build a bounded compatibility proof from retained rehearsal evidence, not flags.

This is a packager/validator, NOT a rehearsal runner or a signature verifier.
The caller must first verify predecessor manifests with production host trust,
resolve target digests/revisions, and run real rehearsals on those exact inputs.
Only reviewed same-run CI evidence may reach the production signing boundary.
A hand-written JSON verdict, a run number, unit tests, or this script succeeding
is NOT evidence that product images are compatible. No waiver option exists.

Inputs: --target is the resolved, default-uncertified manifest; --policy is the
reviewed release policy. --evidence is a directory containing evidence.json,
<raw_manifest_sha256>.json for each predecessor, and the JUnit XML reports.
The envelope has exactly schema_version=1, repository, run_id, inputs_hash and
rows. Each row has predecessor, dependencies, persistence and reports. Reports
maps every compatibility.SUITES name to {path: basename.xml, sha256: hex}.
JUnit testsuite properties must bind inputs_hash, binding_hash, repository and
run_id. binding_hash is manifest_hash of the row excluding reports. Every
required case from required_cases() must execute with positive assertions and
no failure/error/skip. Logs are never copied into the public proof. Synthetic
unit fixtures test this contract only; they must never be release evidence.

Outputs are never replaced. The proof and manifest are deterministic for the
same inputs; a partial local retry can complete matching outputs. Neither is
published or signed here. An empty default policy cannot certify any release.
Python 3.9+ standard library only; never downloads, executes or restores input.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import os
import re
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if "deploy" not in sys.modules:
    _spec = importlib.machinery.ModuleSpec("deploy", None, is_package=True)
    _spec.submodule_search_locations = [str(ROOT / "deploy")]
    sys.modules["deploy"] = importlib.util.module_from_spec(_spec)

from deploy.update.compatibility import (  # noqa: E402
    MAX_PROOF_BYTES, PROOF_ASSET, SUITES, decode_document, inputs_hash,
    require, topology_profile, validate_release_proof,
)
from deploy.update.manifest import (  # noqa: E402
    ManifestError, canonical_json, manifest_hash, validate_manifest,
)

REPOSITORY = "future-agi/future-agi"
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024


def read_bytes(path: Path, limit: int = MAX_PROOF_BYTES) -> bytes:
    """No devices/FIFOs/symlinks; bounded reads, including files grown mid-read."""
    fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
        raw = stream.read(limit + 1)
    require(len(raw) <= limit)
    return raw


def required_cases(suite: str, layout) -> set:
    """Exact coverage, including protocols and nonpersistent state, not counts."""
    if suite == "no_migrations":
        return {"no_pending_migrations"} | {
            action + ":" + domain for domain in layout.domains
            for action in ("unchanged_epoch", "unchanged_format")}
    if suite == "bidirectional_read_write":
        return {action + ":" + domain for domain in layout.domains
                for action in ("old_reads_new", "old_writes_new", "new_reads_old", "new_writes_old")}
    if suite in ("interruption_fallback", "readiness_failure_fallback"):
        return {"failure_injected", "images_only_fallback", "prior_images_ready"} | {
            "state_preserved:" + domain for domain in layout.domains}
    require(suite == "backup_restore")
    return {action + ":" + domain for domain in layout.backup_domains
            for action in ("backup", "restore", "verify_restored")}


def check_report(raw: bytes, suite: str, layout, properties: dict) -> None:
    """Strict JUnit subset: aggregate counters alone never constitute evidence."""
    text = raw.decode("utf-8")
    require("<!" not in text)  # no DTD, entity expansion or alternate content
    root = ET.fromstring(text)
    require(root.tag == "testsuite" and root.attrib.get("name") == suite)
    expected = required_cases(suite, layout)
    for key, value in (("tests", str(len(expected))), ("failures", "0"), ("errors", "0"), ("skipped", "0")):
        require(root.attrib.get(key) == value)
    require(all(child.tag in ("properties", "testcase") for child in root))
    props = root.findall("properties")
    require(len(props) == 1 and len(props[0]) == len(properties))
    observed = {}
    for item in props[0]:
        require(item.tag == "property" and set(item.attrib) == {"name", "value"} and not len(item))
        require(item.attrib["name"] not in observed)
        observed[item.attrib["name"]] = item.attrib["value"]
    require(observed == properties)
    cases = root.findall("testcase")
    require(len(cases) == len(expected))
    names = set()
    for case in cases:
        require(not len(case) and case.attrib.get("classname") == suite)
        require(set(case.attrib) <= {"name", "classname", "assertions", "time"})
        require(re.fullmatch(r"[1-9][0-9]{0,8}", case.attrib.get("assertions", "")) is not None)
        name = case.attrib.get("name")
        require(name in expected and name not in names)
        names.add(name)
    require(names == expected)


def build(target: dict, policy: dict, evidence: Path, *, run_id: int,
          repository: str = REPOSITORY) -> tuple:
    """Return (proof_bytes, certified_manifest_bytes); no writes or side effects."""
    require(repository == REPOSITORY and type(run_id) is int and 1 <= run_id <= 9007199254740991)
    target = decode_document(canonical_json(validate_manifest(target)))
    require(set(policy) == {"schema_version", "migration", "compatibility"}
            and type(policy["schema_version"]) is int and policy["schema_version"] == 1)
    require(type(policy["compatibility"]) is dict and policy["compatibility"].get("validation") is None)
    target.update(schema_version=2, migration=policy["migration"],
                  compatibility=decode_document(canonical_json(policy["compatibility"])))
    compatibility = target["compatibility"]
    require(compatibility.get("automatic_apply") is True and bool(compatibility.get("predecessors")))
    digest = inputs_hash(target)
    compatibility["validation"] = {"asset": PROOF_ASSET, "sha256": "0" * 64,
                                   "inputs_hash": digest, "run_id": run_id}
    validate_manifest(target)
    require(evidence.is_dir() and not evidence.is_symlink())
    envelope = decode_document(read_bytes(evidence / "evidence.json"))
    require(set(envelope) == {"schema_version", "repository", "run_id", "inputs_hash", "rows"})
    require(type(envelope["schema_version"]) is int and envelope["schema_version"] == 1
            and type(envelope["run_id"]) is int and envelope["run_id"] == run_id
            and envelope["repository"] == repository and envelope["inputs_hash"] == digest)
    rows = envelope["rows"]
    require(type(rows) is list and len(rows) == len(compatibility["predecessors"]))
    require(all(type(row) is dict and set(row) == {"predecessor", "dependencies", "persistence", "reports"}
                for row in rows))
    require({canonical_json(row["predecessor"]) for row in rows}
            == {canonical_json(row) for row in compatibility["predecessors"]})
    proof_rows, used_reports, total = [], set(), 0
    for row in rows:
        previous = row["predecessor"]
        raw = read_bytes(evidence / (previous["raw_manifest_sha256"] + ".json"))
        old = validate_manifest(decode_document(raw))
        require(hashlib.sha256(raw).hexdigest() == previous["raw_manifest_sha256"]
                and manifest_hash(old) == previous["manifest_hash"]
                and old["version"] == previous["version"] and old["source_sha"] == previous["source_sha"])
        layout = topology_profile(previous["mode"], previous["profiles"])
        binding = {key: row[key] for key in ("predecessor", "dependencies", "persistence")}
        properties = {"inputs_hash": digest, "binding_hash": manifest_hash(binding),
                      "repository": repository, "run_id": str(run_id)}
        require(type(row["reports"]) is dict and set(row["reports"]) == set(SUITES))
        for suite in SUITES:
            report = row["reports"][suite]
            require(type(report) is dict and set(report) == {"path", "sha256"})
            name = report["path"]
            require(type(name) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.xml", name) is not None)
            require(name not in used_reports)
            used_reports.add(name)
            raw_report = read_bytes(evidence / name)
            total += len(raw_report)
            require(total <= MAX_EVIDENCE_BYTES and hashlib.sha256(raw_report).hexdigest() == report["sha256"])
            check_report(raw_report, suite, layout, properties)
        proof_rows.append({"predecessor": previous, "predecessor_components": old["components"],
                           "predecessor_deployment": old["deployment"], "persistence": row["persistence"],
                           "dependencies": row["dependencies"], "suites": {suite: "passed" for suite in SUITES}})
    proof_rows.sort(key=lambda row: canonical_json(row["predecessor"]))
    proof = {"schema_version": 1, "inputs_hash": digest, "tag": target["version"],
             "source_sha": target["source_sha"], "repository": repository, "run_id": run_id,
             "components": target["components"], "deployment": target["deployment"],
             "state_domains": compatibility["state_domains"], "predecessors": proof_rows}
    raw = canonical_json(proof)
    require(len(raw) <= MAX_PROOF_BYTES)
    compatibility["validation"]["sha256"] = hashlib.sha256(raw).hexdigest()
    validate_manifest(target)
    validate_release_proof(raw, target, repository=repository)
    return raw, canonical_json(target)


def write_pair(outputs: tuple) -> None:
    require(len({path.absolute() for path, _ in outputs}) == len(outputs))
    for path, raw in outputs:
        if path.exists() or path.is_symlink():
            require(read_bytes(path) == raw)
    for path, raw in outputs:
        try:
            with path.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            require(read_bytes(path) == raw)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--repository", default=REPOSITORY, choices=(REPOSITORY,))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw, manifest = build(decode_document(read_bytes(args.target)), decode_document(read_bytes(args.policy)),
                              args.evidence, run_id=args.run_id, repository=args.repository)
        write_pair(((args.output, raw), (args.manifest_output, manifest)))
    except (ManifestError, OSError, ValueError, TypeError, KeyError, RecursionError, ET.ParseError):
        print("compatibility evidence rejected; operator action required", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
