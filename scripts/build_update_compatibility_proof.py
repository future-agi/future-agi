#!/usr/bin/env python3
"""Build a bounded compatibility proof from retained rehearsal evidence, not flags.

This is a packager/validator, NOT a rehearsal runner or a signature verifier.
The caller must first verify predecessor manifests with production host trust,
resolve target digests/revisions, and run real rehearsals on those exact inputs.
Only reviewed same-run CI evidence may reach the production signing boundary.
A hand-written JSON verdict, a run number, unit tests, or this script succeeding
is NOT evidence that product images are compatible. No waiver option exists.
Contract 2 evidence includes exact execution_deployment in each binding, a
producer/candidate content identity, lane and reverified predecessor bundles.
The production CLI accepts only product evidence; synthetic v2 fixtures must
explicitly call build(lane="synthetic") and cannot enter the release DAG.

Inputs: --target is the resolved, default-uncertified manifest; --policy is the
reviewed release policy. --evidence is a directory containing evidence.json,
<raw_manifest_sha256>.json for each predecessor, and the JUnit XML reports.
The v2 envelope binds repository/run, frozen candidate, producer, complete
shipping implementation inventory and the reviewed high-volume registry. Each
row binds exact predecessor/layout/dependencies, migration evidence, A6 measured
rates and private evidence inventory. Reports map all SIX compatibility.SUITES
to {path: basename.xml, sha256: hex}. JUnit properties bind the complete row,
inputs and implementation to the same run. Every required case must execute
with positive assertions and no failure/error/skip. Legacy-v1 fixture parsing
uses LEGACY_SUITES only. Private corpus/logs never enter the bounded public
proof. Synthetic unit fixtures test this contract, never product qualification.

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # fixed trusted checkout, never an artifact directory
if "deploy" not in sys.modules:
    _spec = importlib.machinery.ModuleSpec("deploy", None, is_package=True)
    _spec.submodule_search_locations = [str(ROOT / "deploy")]
    sys.modules["deploy"] = importlib.util.module_from_spec(_spec)

from deploy.update.compatibility import (  # noqa: E402
    MAX_PROOF_BYTES, PROOF_ASSET, SUITES, LEGACY_SUITES, decode_document, inputs_hash,
    require, topology_profile, transition_measurements, validate_release_proof,
)
from deploy.update.manifest import (  # noqa: E402
    ManifestError, canonical_json, manifest_hash, validate_manifest,
)

REPOSITORY = "future-agi/future-agi"
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
MANUAL_REASONS = ('no_supported_signed_predecessor', 'suite_failed', 'suite_unrun',
                  'suite_timed_out', 'shipping_executor_unavailable',
                  'high_volume_reference_unavailable', 'migration_measurement_unavailable',
                  'witness_operation_unavailable', 'platform_not_rehearsed',
                  'initialization_required', 'unsupported_installation')
MARKERS = ('qualification_policy', 'restore_procedure_id', 'backup_inventory_version',
           'host_observation_contract', 'runtime_activation_contract')
MEASURED_FIELDS = ('migration_evidence', 'migration_measurements', 'backup_measurement',
                   'restore_measurement', 'persistent_coverage', 'executor_sha256',
                   'evidence_inventory_sha256')


def implementation_inventory(root: Path = ROOT) -> dict:
    """Complete shipped Python/data closure plus producer/packager entrypoints.

    This deterministic convention is independently recomputed, never read from
    a proof as its own expected value. Runtime consumers must use this SAME
    convention; absent files or a substituted helper cannot borrow a digest.
    No downloaded program, executable discovery hook or manifest path is used.
    """
    paths = [root / 'scripts' / name for name in (
        'rehearse_update_compatibility.py', 'build_update_compatibility_proof.py',
        'generate_update_manifest.py', 'update_rehearsal_shipping.py')]
    paths.extend(root / name for name in ('bin/update', 'deploy/standalone/bin/start',
        'deploy/standalone/bin/bootstrap.py', 'deploy/standalone/nginx.conf'))
    base = root / 'deploy/update'
    require(base.is_dir() and not base.is_symlink())
    def unreadable(error):
        raise error
    for directory, dirs, files in os.walk(base, followlinks=False, onerror=unreadable):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        require(not any((Path(directory) / d).is_symlink() for d in dirs))
        paths.extend(Path(directory) / f for f in sorted(files)
                     if Path(f).suffix in ('.py', '.json', '.txt', '.toml', '.in') or f == 'upgrade-floor')
    return {p.relative_to(root).as_posix(): hashlib.sha256(read_bytes(p, MAX_EVIDENCE_BYTES)).hexdigest()
            for p in sorted(paths)}


def inventory_hash(inventory: dict) -> str:
    require(type(inventory) is dict and bool(inventory))
    return hashlib.sha256(canonical_json(inventory)).hexdigest()


def check_private_inventory(evidence: Path, row: dict) -> dict:
    """Hash the enumerated private corpus, not a claimed number of witnesses."""
    inventory = row['evidence_inventory']
    require(type(inventory) is dict and 1 <= len(inventory) <= 1024)
    require(inventory_hash(inventory) == row['evidence_inventory_sha256'])
    total, documents = 0, {}
    for name, digest in inventory.items():
        require(type(name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.json', name) is not None)
        raw = read_bytes(evidence / name, MAX_EVIDENCE_BYTES)
        total += len(raw)
        require(total <= MAX_EVIDENCE_BYTES and hashlib.sha256(raw).hexdigest() == digest)
        documents[name] = decode_document(raw, MAX_EVIDENCE_BYTES)  # duplicate/non-finite JSON refuses
    return documents


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
    """Exact executed assertions. Legacy cases only describe readable v1 data."""
    domains = set(layout.backup_domains)
    if suite == 'source_snapshot':
        return {'source_identity', 'source_migrations_exact', 'snapshot_sealed',
                'persistent_domain_mapping', 'witness_namespace_complete', 'large_volume_reference'} | {
                    'source_witness:' + d for d in domains}
    if suite == 'migration_forward':
        return {'pending_plan_measured', 'pending_plan_applied_exact', 'target_migrations_exact',
                'per_migration_measurements', 'large_volume_migrations'} | {
                    action + ':' + d for d in domains for action in ('new_reads_old', 'target_create_read')}
    if suite == 'target_readiness':
        return {'normal_compose_direct_start', 'health', 'restart_count_stability',
                'final_identities', 'redis_ephemeral'}
    if suite in ('failure_restore', 'interruption_restore'):
        common = {'same_volume_objects', 'shipping_restore', 'source_ready', 'source_migrations_exact',
                  'candidate_only_absent', 'restored_namespace_complete'} | {
                      'restored_witness:' + d for d in domains}
        return common | ({'failure_after_migration'} if suite == 'failure_restore' else {
            'updater_process_terminated', 'fresh_recovery_process', 'durable_boundaries'})
    if suite == 'backup_integrity':
        return {'full_extracted_trees', 'configuration_exact', 'backup_restore_throughput'} | {
            'refuses:' + kind for kind in ('tampered', 'truncated', 'substituted', 'incomplete')}
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
          repository: str = REPOSITORY, lane: str = "product", verifier=None) -> tuple:
    """Return (proof_bytes, certified_manifest_bytes); no writes or side effects."""
    require(repository == REPOSITORY and type(run_id) is int and 1 <= run_id <= 9007199254740991)
    require(lane in ("product", "synthetic"))
    candidate_sha256 = hashlib.sha256(canonical_json(validate_manifest(target))).hexdigest()
    frozen_candidate = decode_document(canonical_json(target))
    target = decode_document(canonical_json(target))
    require(set(policy) == {"schema_version", "migration", "compatibility"}
            and type(policy["schema_version"]) is int and policy["schema_version"] == 1)
    require(type(policy["compatibility"]) is dict and policy["compatibility"].get("validation") is None)
    require(policy['migration'] == target['migration'])  # frozen legacy bytes are NOT derived eligibility
    target.update(schema_version=2, compatibility=decode_document(canonical_json(policy["compatibility"])))
    compatibility = target["compatibility"]
    require(compatibility.get("automatic_apply") is True and bool(compatibility.get("predecessors")))
    digest = inputs_hash(target)
    compatibility["validation"] = {"asset": PROOF_ASSET, "sha256": "0" * 64,
                                   "inputs_hash": digest, "run_id": run_id}
    validate_manifest(target)
    require(evidence.is_dir() and not evidence.is_symlink())
    envelope = decode_document(read_bytes(evidence / "evidence.json"))
    contract = compatibility["contract_version"]
    require(lane != 'product' or contract == 2)  # v1 parsing/fixtures cannot issue new shipping qualification
    suites = SUITES if contract == 2 else LEGACY_SUITES
    extra = {"lane", "candidate_sha256", "producer_sha256", 'executor_inventory',
             'executor_sha256', 'high_volume_registry_sha256'} if contract == 2 else set()
    require(set(envelope) == {"schema_version", "repository", "run_id", "inputs_hash", "rows"} | extra)
    require(type(envelope["schema_version"]) is int and envelope["schema_version"] == contract
            and type(envelope["run_id"]) is int and envelope["run_id"] == run_id
            and envelope["repository"] == repository and envelope["inputs_hash"] == digest)
    if contract == 2:
        require(envelope["lane"] == lane and envelope["candidate_sha256"] == candidate_sha256)
        require(envelope["producer_sha256"] == hashlib.sha256(
            read_bytes(ROOT / "scripts/rehearse_update_compatibility.py", MAX_EVIDENCE_BYTES)).hexdigest())
        require(envelope['executor_inventory'] == implementation_inventory())
        require(envelope['executor_sha256'] == inventory_hash(envelope['executor_inventory']))
        require(envelope['high_volume_registry_sha256'] == hashlib.sha256(
            read_bytes(ROOT / 'deploy/update/high-volume-tables.json', MAX_EVIDENCE_BYTES)).hexdigest())
        if lane == "product":
            from deploy.update.trust import Verifier
            require(isinstance(verifier, Verifier) and verifier.host_only)
    rows = envelope["rows"]
    require(type(rows) is list and 1 <= len(rows) <= 64 and len(rows) == len(compatibility["predecessors"]))
    binding_keys = ("predecessor", "dependencies", "persistence") + (
        ("execution_deployment",) + MEASURED_FIELDS + ('evidence_inventory',) if contract == 2 else ())
    require(all(type(row) is dict and set(row) == set(binding_keys) | {"reports"} for row in rows))
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
        if contract == 2:
            require(manifest_hash(row["execution_deployment"]) == previous["execution_deployment_hash"])
            require(row['executor_sha256'] == envelope['executor_sha256'])
            transition_measurements(row)  # exact ordered plan, finite metrics, same reference snapshot
            documents = check_private_inventory(evidence, row)
            if lane == "product":
                # These imports execute only our checked-in producer, never an
                # evidence artifact. Product metrics must reduce from retained
                # raw samples; synthetic fixtures deliberately use no product data.
                from scripts.rehearse_update_compatibility import (RehearsalError,
                    validate_executed_measurements, witness_inventory)
                require(all(re.fullmatch(r'[0-9]+-[a-z][a-z0-9_]{0,60}\.json', name) for name in documents))
                require(all(type(value) is dict and set(value) == {'value'} for value in documents.values()))
                corpus = {name.split('-', 1)[1][:-5]: value['value'] for name, value in documents.items()}
                require(len(corpus) == len(documents) and {'source_witnesses', 'restored_witnesses'} <= set(corpus))
                try:
                    validate_executed_measurements(row, corpus, target=frozen_candidate, predecessor_raw=raw)
                    require(witness_inventory(corpus['source_witnesses']) == witness_inventory(corpus['restored_witnesses']))
                except RehearsalError:
                    require(False)
                from deploy.update.trust import FetchedManifest
                bundle = evidence / (previous["raw_manifest_sha256"] + ".sigstore.json")
                read_bytes(bundle, 1024 * 1024)
                fetched = FetchedManifest("retained-predecessor", evidence,
                    evidence / (previous["raw_manifest_sha256"] + ".json"), raw, old, bundle)
                require(verifier.verify(fetched).verified)
        layout = topology_profile(previous["mode"], previous["profiles"])
        binding = {key: row[key] for key in binding_keys}
        properties = {"inputs_hash": digest, "binding_hash": manifest_hash(binding),
                      "repository": repository, "run_id": str(run_id)}
        if contract == 2:
            properties.update({key: envelope[key] for key in extra if key != 'executor_inventory'})
        require(type(row["reports"]) is dict and set(row["reports"]) == set(suites))
        for suite in suites:
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
        proof_row = {"predecessor": previous, "predecessor_components": old["components"],
                     "predecessor_deployment": old["deployment"], "persistence": row["persistence"],
                     "dependencies": row["dependencies"], "suites": {suite: "passed" for suite in suites}}
        if contract == 2:
            proof_row["execution_deployment"] = row["execution_deployment"]
            proof_row.update({key: row[key] for key in MEASURED_FIELDS})
        proof_rows.append(proof_row)
    proof_rows.sort(key=lambda row: canonical_json(row["predecessor"]))
    proof = {"schema_version": contract, "inputs_hash": digest, "tag": target["version"],
             "source_sha": target["source_sha"], "repository": repository, "run_id": run_id,
             "components": target["components"], "deployment": target["deployment"], "predecessors": proof_rows}
    if contract == 2:
        proof.update({key: compatibility[key] for key in MARKERS})
    else:
        proof['state_domains'] = compatibility['state_domains']
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
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            require(read_bytes(path) == raw)


def manual_candidate(target: dict, decision: dict, *, run_id: int) -> bytes:
    """Keep a positively classified incompatible release manual, never eligible.

    The same-job caller supplies the producer's bounded diagnostic. Missing or
    failed evidence has no such output and cannot silently become this path.
    Even forged diagnostics cannot add affirmative compatibility or a proof.
    """
    target = validate_manifest(target)
    require(type(run_id) is int and 1 <= run_id <= 9007199254740991)
    require(set(decision) == {'schema_version','eligible','reason','candidate_sha256','run_id','repository'})
    require(type(decision['schema_version']) is int and decision['schema_version']==1
            and decision['eligible'] is False and decision['reason'] in MANUAL_REASONS
            and type(decision['run_id']) is int and decision['run_id']==run_id
            and decision['repository']==REPOSITORY
            and decision['candidate_sha256']==hashlib.sha256(canonical_json(target)).hexdigest())
    compatibility = target.get('compatibility', {})
    require(compatibility.get('automatic_apply') is False and compatibility.get('validation') is None
            and compatibility.get('automatic_rollback') == {'strategy':'never'})
    return canonical_json(target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--manual-decision", type=Path, help="same-job incompatibility diagnostic; NEVER emits a proof")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--repository", default=REPOSITORY, choices=(REPOSITORY,))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.manual_decision:
            require(args.policy is None and args.evidence is None and not args.output.exists() and not args.output.is_symlink())
            manifest = manual_candidate(decode_document(read_bytes(args.target)),
                decode_document(read_bytes(args.manual_decision)), run_id=args.run_id)
            write_pair(((args.manifest_output, manifest),))
            return 0
        require(args.policy is not None and args.evidence is not None)
        from deploy.update.settings import load_settings
        from deploy.update.trust import Verifier
        raw, manifest = build(decode_document(read_bytes(args.target)), decode_document(read_bytes(args.policy)),
                              args.evidence, run_id=args.run_id, repository=args.repository,
                              verifier=Verifier(load_settings(ROOT, environ={}), host_only=True))
        write_pair(((args.output, raw), (args.manifest_output, manifest)))
    except (ManifestError, OSError, ValueError, TypeError, KeyError, RecursionError, ET.ParseError):
        print("compatibility evidence rejected; operator action required", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
