"""S02 pure contract tests. ALL certificates/images/run IDs here are SYNTHETIC.

No real release is qualified, signatures are not manufactured, and no daemon,
network, filesystem qualification or real release-proof acquisition is mocked
as an integration pass. The future in-lock adapters supply actual observations.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.tests.test_update_manifest import valid_manifest  # noqa: E402
from deploy.update.compatibility import (  # noqa: E402
    MAX_PROOF_BYTES, PROOF_ASSET, LEGACY_SUITES as SUITES, SUITES as RESTORE_SUITES, CompatibilityError, Controls,
    Observations, RuntimeImage, decode_document, evaluate_automatic_apply,
    inputs_hash, schema, shape, topology_profile,
    validate_release_proof, transition_measurements, estimate_automatic_update,
)
from deploy.update.contracts import (  # noqa: E402
    AutomationPolicy, InstallationBinding, PreparedUpdate, ReasonCode, VerificationSnapshot,
    AuthorisedTransition, BackupSeal, ColdHold, RuntimeObservationV2, RestorationResult, ContractError,
    installation_binding_hash, pending_migration_plan_hash, HostSizeObservation, HostTableSize,
    TransitionMeasurements, AUTOMATIC_VOLUMES, AUTOMATIC_POLICY_BOUNDS, OperationEvent,
)
from deploy.update.manifest import ManifestError, canonical_json, manifest_hash, validate_manifest  # noqa: E402
from deploy.update.settings import DEFAULT_TRUSTED_IDENTITY, DEFAULT_TRUSTED_ISSUER  # noqa: E402

needs_jsonschema = unittest.skipUnless(importlib.util.find_spec("jsonschema"), "optional jsonschema is not installed")
NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
RESERVED = NOW - timedelta(seconds=120)


def stamp(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def default_v2(version="v1.52.0"):
    target = valid_manifest(version)
    target["schema_version"] = 2
    target["compatibility"] = json.loads((ROOT / "deploy/update/release-policy.json").read_text())["compatibility"]
    return target


def bind_proof(target, proof):
    """Synthetic fixture signing-boundary simulation, NOT a real signature."""
    proof["inputs_hash"] = inputs_hash(target)
    raw = canonical_json(proof)
    target["compatibility"]["validation"] = {
        "asset": PROOF_ASSET, "sha256": hashlib.sha256(raw).hexdigest(),
        "inputs_hash": proof["inputs_hash"], "run_id": proof["run_id"],
    }
    return raw


def synthetic_release(mode="standalone", profiles=(), platform="linux/amd64"):
    old, target = valid_manifest("v1.51.0"), default_v2()
    # Readable historical contract-v1 fixture retained for legacy consumer tests.
    target["compatibility"] = {"contract_version": 1, "automatic_apply": False,
        "requires_operator_initialization": True, "required_env": [], "state_domains": [], "predecessors": [],
        "automatic_rollback": {"strategy": "never"}, "validation": None}
    old["source_sha"] = "b" * 40
    old_raw = canonical_json(old)
    target["migration"]["requires_bootstrap"] = False
    layout = topology_profile(mode, profiles)
    predecessor = {"version": old["version"], "manifest_hash": manifest_hash(old),
                   "raw_manifest_sha256": hashlib.sha256(old_raw).hexdigest(), "source_sha": old["source_sha"],
                   "mode": mode, "platform": platform, "profiles": list(profiles),
                   "active_components": list(layout.active_components), "rollback_certified": True}
    domains = [{"domain": domain, "from_epoch": 7, "to_epoch": 7, "backward_read": True, "backward_write": True}
               for domain in layout.domains]
    target["compatibility"].update({"automatic_apply": True, "requires_operator_initialization": False,
                                    "state_domains": domains, "predecessors": [predecessor],
                                    "automatic_rollback": {"strategy": "images_only"}})
    proof = {"schema_version": 1, "inputs_hash": "0" * 64, "tag": target["version"],
             "source_sha": target["source_sha"], "repository": "future-agi/future-agi", "run_id": 42,
             "components": copy.deepcopy(target["components"]), "deployment": copy.deepcopy(target["deployment"]),
             "state_domains": copy.deepcopy(domains), "predecessors": [{
                 "predecessor": copy.deepcopy(predecessor), "predecessor_components": copy.deepcopy(old["components"]),
                 "predecessor_deployment": copy.deepcopy(old["deployment"]), "persistence": dict(layout.persistence),
                 "dependencies": [{"service": service, "image": "docker.io/library/synthetic-" + service + ":1",
                                   "digest": "sha256:" + "c" * 64} for service in layout.dependencies],
                 "suites": {suite: "passed" for suite in SUITES},
             }]}
    raw = bind_proof(target, proof)
    return target, old_raw, proof, raw


def verification(version):
    # Caller-provided verifier observation for a pure unit test only.
    return VerificationSnapshot(True, "keyless", DEFAULT_TRUSTED_IDENTITY.format(version=version),
                                DEFAULT_TRUSTED_ISSUER, "synthetic-test-verifier", NOW)


def prepare(target, mode="standalone", profiles=()):
    raw = canonical_json(target)
    binding = InstallationBinding("a" * 32, "b" * 64, "c" * 64, Path("/synthetic/checkout"),
                                  Path("/synthetic/env"), Path("/synthetic/env"), mode, False, "synthetic",
                                  (Path("/synthetic/compose"),), tuple(profiles), "synthetic-context", "synthetic-daemon", 1)
    plan = {"decision": "update_available", "resume": False, "target_version": target["version"],
            "manifest_hash": manifest_hash(target), "reasons": [], "deployment_drift": [],
            "missing_env_keys": [], "staged_version": None, "stopped_version": None}
    return PreparedUpdate(target, raw, b"synthetic-not-a-signature", hashlib.sha256(raw).hexdigest(),
                          hashlib.sha256(b"synthetic-not-a-signature").hexdigest(), verification(target["version"]),
                          binding, "d" * 64, "e" * 64, "f" * 64, "1" * 64, plan)


def runtime_images(components, layout):
    return {service: (RuntimeImage(components[component]["image"], components[component]["digest"], "sha256:" + "d" * 64),)
            for service, component in layout.products}


def synthetic_context(mode="standalone", profiles=()):
    target, old_raw, proof, raw = synthetic_release(mode, profiles)
    prepared = prepare(target, mode, profiles)
    layout = topology_profile(mode, profiles)
    old = json.loads(old_raw)
    obs = Observations(NOW, predecessor_raw=old_raw, predecessor_verification=verification(old["version"]),
                       release_proof=raw, platform="linux/amd64",
                       product_images=runtime_images(old["components"], layout),
                       dependency_images={item["service"]: (RuntimeImage(item["image"], item["digest"], "sha256:" + "e" * 64),)
                                          for item in proof["predecessors"][0]["dependencies"]},
                       state_epochs={domain: 7 for domain in layout.domains}, persistence=dict(layout.persistence),
                       deployment_files=target["deployment"]["files"], installation_fingerprint="c" * 64,
                       config_fingerprint="d" * 64, profile_fingerprint="e" * 64, trust_fingerprint="1" * 64,
                       attempt_id="a" * 32, reserved_at=RESERVED, online_verified=True, owner_confirmed=True,
                       topology_qualified=True, runtime_complete=True, baseline_attested=True, state_unchanged=True,
                       graph_unchanged=True, no_pending_transaction=True, no_failed_target=True, env_unchanged=True,
                       no_source_build=True, no_pending_migrations=True)
    controls = Controls(AutomationPolicy(mode="apply", enabled=True, automatic_rollback=True), enabled=True,
                        paused=False, registered=True, window_open=True, start_budget_available=True)
    backup = {"schema_version": 1, "attempt_id": obs.attempt_id,
              "installation_fingerprint": obs.installation_fingerprint, "target_manifest_hash": manifest_hash(target),
              "source_version": old["version"], "backup_id": "synthetic-backup", "started_at": stamp(RESERVED),
              "completed_at": stamp(NOW - timedelta(seconds=60)), "covered_domains": list(layout.backup_domains),
              "verification": "passed", "restore_procedure_id": "synthetic-tested-restore"}
    return prepared, obs, controls, canonical_json(backup)


class ManifestV2Tests(unittest.TestCase):
    def test_v1_and_default_v2_are_backwards_readable_but_not_certified(self):
        self.assertEqual(validate_manifest(valid_manifest())["schema_version"], 1)
        target = validate_manifest(default_v2())
        self.assertFalse(target["compatibility"]["automatic_apply"])
        self.assertIsNone(target["compatibility"]["validation"])

    def test_compatibility_required_only_in_v2(self):
        target = valid_manifest()
        target["schema_version"] = 2
        with self.assertRaises(ManifestError):
            validate_manifest(target)
        target = default_v2()
        target["schema_version"] = 1
        with self.assertRaises(ManifestError):
            validate_manifest(target)

    def test_synthetic_positive_metadata_and_derived_hash(self):
        target, _, _, raw = synthetic_release()
        self.assertEqual(validate_manifest(target), target)
        self.assertEqual(validate_release_proof(raw, target, repository="future-agi/future-agi")["run_id"], 42)
        source = {key: copy.deepcopy(target[key]) for key in ("version", "source_sha", "components", "helm", "deployment", "compatibility")}
        del source["compatibility"]["validation"]
        self.assertEqual(inputs_hash(target), hashlib.sha256(canonical_json(source)).hexdigest())

    def test_hash_is_deterministic_and_only_validation_is_omitted(self):
        target, _, _, _ = synthetic_release()
        expected = inputs_hash(target)
        reordered = dict(reversed(list(target.items())))
        self.assertEqual(inputs_hash(reordered), expected)
        target["compatibility"]["validation"]["sha256"] = "f" * 64
        self.assertEqual(inputs_hash(target), expected)
        target["components"]["backend"]["digest"] = "sha256:" + "f" * 64
        self.assertNotEqual(inputs_hash(target), expected)

    def test_each_affected_domain_read_write_and_epoch_is_required(self):
        for mode in ("standalone", "distributed"):
            base, _, _, _ = synthetic_release(mode)
            for index in range(len(base["compatibility"]["state_domains"])):
                for change in ("missing", "backward_read", "backward_write", "epoch"):
                    with self.subTest(mode=mode, index=index, change=change):
                        target = copy.deepcopy(base)
                        domains = target["compatibility"]["state_domains"]
                        if change == "missing":
                            domains.pop(index)
                        elif change == "epoch":
                            domains[index]["to_epoch"] += 1
                        else:
                            domains[index][change] = False
                        target["compatibility"]["validation"]["inputs_hash"] = inputs_hash(target)
                        with self.assertRaises(ManifestError):
                            validate_manifest(target)

    def test_ambiguous_duplicate_domain_env_and_predecessor_rejected(self):
        for section in ("state_domains", "predecessors", "required_env"):
            target, _, _, _ = synthetic_release()
            if section == "required_env":
                target["compatibility"][section] = [{"name": "TOKEN", "rule": "present", "components": ["standalone"]}]
            target["compatibility"][section].append(copy.deepcopy(target["compatibility"][section][0]))
            target["compatibility"]["validation"]["inputs_hash"] = inputs_hash(target)
            with self.subTest(section=section), self.assertRaises(ManifestError):
                validate_manifest(target)

    def test_auto_flags_never_override_bootstrap_or_initialization(self):
        for section, key in (("migration", "requires_bootstrap"), ("compatibility", "requires_operator_initialization")):
            target, _, _, _ = synthetic_release()
            target[section][key] = True
            target["compatibility"]["validation"]["inputs_hash"] = inputs_hash(target)
            with self.assertRaises(ManifestError):
                validate_manifest(target)

    def test_no_empty_or_absent_proof_affirmative_metadata(self):
        for field, value in (("state_domains", []), ("predecessors", []), ("validation", None)):
            target, _, _, _ = synthetic_release()
            target["compatibility"][field] = value
            with self.subTest(field=field), self.assertRaises(ManifestError):
                validate_manifest(target)

    def test_unknown_nested_and_missing_fields(self):
        target, _, _, _ = synthetic_release()
        def walk(value, path=()):
            if isinstance(value, dict):
                yield path
                for key, child in value.items():
                    yield from walk(child, path + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from walk(child, path + (index,))
        for path in walk(target["compatibility"]):
            damaged = copy.deepcopy(target)
            node = damaged["compatibility"]
            for key in path:
                node = node[key]
            node["unexpected"] = "SECRET /private/backup command"
            with self.subTest(path=path), self.assertRaises(CompatibilityError) as caught:
                validate_manifest(damaged)
            self.assertNotIn("SECRET", str(caught.exception))
        for key in target["compatibility"]:
            damaged = copy.deepcopy(target)
            del damaged["compatibility"][key]
            with self.subTest(missing=key), self.assertRaises(ManifestError):
                validate_manifest(damaged)

    def test_unknown_profile_component_platform_and_future_predecessor(self):
        for field, value in (("profiles", ["custom"]), ("active_components", ["backend"]),
                             ("platform", "windows/amd64"), ("version", "v2.0.0")):
            target, _, _, _ = synthetic_release()
            target["compatibility"]["predecessors"][0][field] = value
            target["compatibility"]["validation"]["inputs_hash"] = inputs_hash(target)
            with self.subTest(field=field), self.assertRaises(ManifestError):
                validate_manifest(target)

    @needs_jsonschema
    def test_v2_json_schema_structural_parity(self):
        from jsonschema import Draft202012Validator
        spec = schema("release-manifest.schema.json")
        Draft202012Validator.check_schema(spec)
        validator = Draft202012Validator(spec)
        target, _, _, _ = synthetic_release()
        for value in (target, default_v2(), valid_manifest()):
            validator.validate(value)
            validate_manifest(value)
        for key in target["compatibility"]:
            bad = copy.deepcopy(target)
            del bad["compatibility"][key]
            self.assertFalse(validator.is_valid(bad), key)
            with self.assertRaises(ManifestError):
                validate_manifest(bad)
        for field, value in (("contract_version", True), ("state_domains", ["invalid"]),
                             ("automatic_apply", 1), ("validation", {}), ("unexpected", None)):
            bad = copy.deepcopy(target)
            bad["compatibility"][field] = value
            self.assertFalse(validator.is_valid(bad), field)
            with self.assertRaises(ManifestError):
                validate_manifest(bad)


class ReleaseProofTests(unittest.TestCase):
    def test_all_builtin_topologies_profiles_have_synthetic_matching_proofs(self):
        for mode, profiles in (("standalone", ()), ("standalone", ("ml", "sandbox")),
                               ("distributed", ()), ("distributed", ("workers",)),
                               ("distributed", ("peerdb", "observability")), ("distributed", ("full",)),
                               ("distributed", ("all",))):
            with self.subTest(mode=mode, profiles=profiles):
                target, _, _, raw = synthetic_release(mode, profiles)
                validate_manifest(target)
                validate_release_proof(raw, target, repository="future-agi/future-agi")

    def test_service_inventory_matches_checked_in_compose_with_inherited_profiles(self):
        # Independent source conformance, not a fixture built from the mapping
        # under test. No daemon, network, optional YAML package or FS mocks.
        from deploy.tests.test_update_release_static import MiniYaml
        from deploy.update.manifest import COMPONENT_REPOSITORIES

        class ComposeYaml(MiniYaml):
            # Compose also places flow values on the line after `test:`.
            # The workflow helper already parses flows, but only when inline.
            def _value(self, rest, index, indent):
                if not rest:
                    following = self._skip(index + 1)
                    if (following < self.n and self.indent[following] > indent
                            and self.content[following][:1] in ("[", "{")):
                        return self._flow(self.content[following], following)
                return super()._value(rest, index, indent)

        def merged(service):
            result = {}
            parents = service.get("<<", [])
            if isinstance(parents, dict):
                parents = [parents]
            for parent in reversed(parents):
                result.update(merged(parent))
            result.update({key: value for key, value in service.items() if key != "<<"})
            return result

        for mode, combinations in (("standalone", [(), ("ml",), ("sandbox",), ("ml", "sandbox")]),
                                   ("distributed", [(), ("workers",), ("peerdb",), ("observability",),
                                                    ("all",), ("full",), ("workers", "peerdb", "observability")])):
            path = ROOT / ("docker-compose.yml" if mode == "standalone" else "docker-compose.distributed.yml")
            services = ComposeYaml(path.read_text()).parse()["services"]
            for profiles in combinations:
                products, dependencies = {}, set()
                for name, declaration in services.items():
                    service = merged(declaration)
                    if service.get("profiles") and not set(service["profiles"]) & set(profiles):
                        continue
                    image = service["image"]
                    component = next((component for component, repository in COMPONENT_REPOSITORIES.items()
                                      if component != "serving_gpu" and image.startswith(repository + ":")), None)
                    if component is None:
                        dependencies.add(name)
                    else:
                        products[name] = component
                layout = topology_profile(mode, profiles)
                with self.subTest(mode=mode, profiles=profiles):
                    self.assertEqual(dict(layout.products), products)
                    self.assertEqual(set(layout.dependencies), dependencies)

    def test_persistence_mapping_is_explicit_not_all_services_are_backup_domains(self):
        standalone = topology_profile("standalone", ())
        self.assertIn("redis", standalone.domains)
        self.assertNotIn("redis", standalone.backup_domains)
        self.assertNotIn("component_protocols", standalone.backup_domains)
        distributed = topology_profile("distributed", ())
        self.assertEqual(set(distributed.backup_domains), {"postgres_app", "clickhouse", "object_store", "temporal",
                         "peerdb_catalog", "peerdb_temporal", "peerdb_objects", "redis", "kafka_catalog", "shared_files"})

    def test_raw_digest_tamper_whitespace_and_extra_bytes_fail(self):
        target, _, _, raw = synthetic_release()
        for damaged in (raw + b"\n", raw.replace(b'"passed"', b'"failed"', 1), raw + b"{}"):
            with self.assertRaises(ManifestError):
                validate_release_proof(damaged, target, repository="future-agi/future-agi")

    def test_each_suite_failed_skipped_or_unknown_cannot_certify(self):
        for suite in SUITES:
            for outcome in ("failed", "skipped", "unknown"):
                target, _, proof, _ = synthetic_release()
                proof["predecessors"][0]["suites"][suite] = outcome
                raw = bind_proof(target, proof)
                with self.subTest(suite=suite, outcome=outcome), self.assertRaises(ManifestError):
                    validate_release_proof(raw, target, repository="future-agi/future-agi")

    def test_missing_duplicate_dependency_and_changed_persistence_fail(self):
        for kind in ("missing", "duplicate", "extra", "persistence"):
            target, _, proof, _ = synthetic_release()
            row = proof["predecessors"][0]
            if kind == "missing":
                row["dependencies"].pop()
            elif kind == "duplicate":
                row["dependencies"][1] = copy.deepcopy(row["dependencies"][0])
            elif kind == "extra":
                row["dependencies"].append({"service": "custom", "image": "docker.io/library/custom:1", "digest": "sha256:" + "a" * 64})
            else:
                row["persistence"]["redis"] = True
            raw = bind_proof(target, proof)
            with self.subTest(kind=kind), self.assertRaises(ManifestError):
                validate_release_proof(raw, target, repository="future-agi/future-agi")

    def test_tag_source_repo_run_and_hash_are_exact(self):
        for key, value in (("tag", "v9.0.0"), ("source_sha", "f" * 40), ("repository", "attacker/fork"),
                           ("run_id", 43), ("inputs_hash", "f" * 64)):
            target, _, proof, _ = synthetic_release()
            proof[key] = value
            raw = canonical_json(proof)
            target["compatibility"]["validation"]["sha256"] = hashlib.sha256(raw).hexdigest()
            with self.subTest(key=key), self.assertRaises(ManifestError):
                validate_release_proof(raw, target, repository="future-agi/future-agi")

    def test_component_and_deployment_proof_bindings(self):
        for key in ("components", "deployment", "state_domains", "predecessor_components", "predecessor_deployment"):
            target, _, proof, _ = synthetic_release()
            if key == "components":
                proof[key]["standalone"]["digest"] = "sha256:" + "a" * 64
            elif key == "state_domains":
                proof[key][0]["from_epoch"] = 8
            elif key == "predecessor_components":
                proof["predecessors"][0][key]["standalone"]["image"] = "attacker/standalone:v1.51.0"
            else:
                node = proof if key == "deployment" else proof["predecessors"][0]
                node[key]["files"]["docker-compose.yml"] = "f" * 64
            raw = bind_proof(target, proof)
            with self.subTest(key=key), self.assertRaises(ManifestError):
                validate_release_proof(raw, target, repository="future-agi/future-agi")

    def test_bounded_strict_json_decoder(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'\xff', b'[]', b'{}' + b' ' * MAX_PROOF_BYTES,
                    b'{"x":' + b'[' * 50 + b'0' + b']' * 50 + b'}'):
            with self.subTest(raw=raw[:40]), self.assertRaises(CompatibilityError):
                decode_document(raw)

    @needs_jsonschema
    def test_proof_schema_and_runtime_shape_parity(self):
        from jsonschema import Draft202012Validator
        spec = schema("compatibility-proof.schema.json")
        Draft202012Validator.check_schema(spec)
        validator = Draft202012Validator(spec)
        _, _, proof, _ = synthetic_release()
        validator.validate(proof)
        shape(proof, spec)
        for key in proof:
            bad = copy.deepcopy(proof)
            del bad[key]
            self.assertFalse(validator.is_valid(bad), key)
            with self.assertRaises(CompatibilityError):
                shape(bad, spec)
        for key, value in (("schema_version", True), ("run_id", 0), ("repository", "https://evil.example/proof"),
                           ("predecessors", []), ("unknown", "SECRET")):
            bad = copy.deepcopy(proof)
            bad[key] = value
            self.assertFalse(validator.is_valid(bad))
            with self.assertRaises(CompatibilityError):
                shape(bad, spec)


def restore_release(pending_count=3):
    """Synthetic v2 contract fixture only, NOT product qualification."""
    legacy, old_raw, legacy_proof, _ = synthetic_release()
    target = default_v2()
    target["migration"]["requires_bootstrap"] = True  # Frozen legacy metadata survives.
    predecessor = copy.deepcopy(legacy["compatibility"]["predecessors"][0])
    predecessor.pop("rollback_certified")
    predecessor.update(restore_certified=True, execution_deployment_hash=manifest_hash(target["deployment"]))
    target["compatibility"].update(automatic_apply=True, requires_operator_initialization=False,
                                    predecessors=[predecessor], automatic_rollback={"strategy": "restore_backup"})
    row = copy.deepcopy(legacy_proof["predecessors"][0])
    row.update(predecessor=copy.deepcopy(predecessor), execution_deployment=copy.deepcopy(target["deployment"]),
               migration_evidence={"source_inventory_sha256": "1" * 64, "target_inventory_sha256": "2" * 64,
                   "source_applied_set_sha256": "3" * 64, "target_applied_set_sha256": "4" * 64,
                   "active_app_mapping_sha256": "5" * 64, "target_pending_plan_sha256": "6" * 64,
                   "target_pending_plan_count": pending_count, "migration_handling": "standalone_bootstrap"},
               persistent_coverage=list(topology_profile("standalone", ()).backup_domains),
               executor_sha256="7" * 64, evidence_inventory_sha256="8" * 64,
               suites={name: "passed" for name in RESTORE_SUITES})
    # Keep adversarial huge-count fixtures small: counts > 10 intentionally lack
    # full measurements and must refuse, rather than allocate huge fake proofs.
    row["migration_measurements"] = [{"app_label": "synthetic_app", "name": "%04d_synthetic_migration" % i,
        "engine": "postgres", "tables": [{"table": "public.synthetic_table", "reference_rows": 100, "reference_bytes": 1000}],
        "risk_class": "rewrite", "duration_seconds": 5, "throughput": {"unit": "rows_per_second", "value": 20},
        "peak_disk_amplification_factor": 2, "wal_bytes": 100, "temp_bytes": 50}
        for i in range(min(pending_count, 10))]
    row["migration_evidence"]["target_pending_plan_sha256"] = pending_migration_plan_hash(
        [{"app_label": m["app_label"], "name": m["name"]} for m in row["migration_measurements"]])
    row["backup_measurement"] = {"reference_volume_bytes": {name: 1000 for name in AUTOMATIC_VOLUMES},
                                 "reference_volume_members": {name: 1 for name in AUTOMATIC_VOLUMES},
                                 "throughput_bytes_per_second": 1000}
    row["restore_measurement"] = copy.deepcopy(row["backup_measurement"])
    proof = {key: copy.deepcopy(legacy_proof[key]) for key in legacy_proof if key != "state_domains"}
    proof.update(schema_version=2, predecessors=[row])
    for key in ("qualification_policy", "restore_procedure_id", "backup_inventory_version",
                "host_observation_contract", "runtime_activation_contract"):
        proof[key] = target["compatibility"][key]
    raw = bind_proof(target, proof)
    return target, old_raw, proof, raw


def restore_context(pending_count=3):
    target, old_raw, proof, raw = restore_release(pending_count)
    prepared = prepare(target)
    _, obs, controls, backup_raw = synthetic_context()
    cid = "9" * 64
    source = RuntimeObservationV2(
        release_version=json.loads(old_raw)["version"], manifest_sha256=hashlib.sha256(old_raw).hexdigest(),
        observed_at=RESERVED, binding_sha256=installation_binding_hash(prepared.binding), platform="linux/amd64",
        product_images=obs.product_images, dependency_images=obs.dependency_images,
        migration_inventory_sha256="1" * 64, applied_migrations_sha256="3" * 64,
        active_app_mapping_sha256="5" * 64, deployment_sha256=manifest_hash(target["deployment"]),
        env_sha256="b" * 64, config_sha256="c" * 64, storage_sha256="d" * 64, container_roster_sha256="e" * 64,
        restart_counts={cid: 0}, restart_policies={cid: "unless-stopped"}, ready=True, stable=True)
    snapshot = RESERVED + timedelta(seconds=1)
    hold = ColdHold(obs.attempt_id, "synthetic-token", "synthetic-run", source.binding_sha256,
                    source.sha256, "f" * 64, snapshot, NOW, prepared.binding.daemon_identity,
                    source.storage_sha256, (cid,), source.restart_counts, source.restart_policies)
    backup = json.loads(backup_raw)
    backup.update(schema_version=2, target_manifest_hash=manifest_hash(target),
                  binding_sha256=source.binding_sha256, inventory_sha256="b" * 64,
                  source_observation_sha256=source.sha256, quiescence_evidence_sha256=hold.quiescence_evidence_sha256,
                  snapshot_at=stamp(snapshot), inventory_version=2,
                  restore_procedure_id="standalone-inplace-cold-restore-v1")
    backup_raw = canonical_json(backup)
    seal = BackupSeal(obs.attempt_id, hold.operation_token, hold.run_id, source.binding_sha256,
                      manifest_hash(target), source.release_version, hashlib.sha256(backup_raw).hexdigest(),
                      backup["inventory_sha256"], source.sha256, hold.quiescence_evidence_sha256, snapshot, NOW)
    obs = replace(obs, predecessor_raw=old_raw, release_proof=raw, runtime_observation=source,
                  cold_hold=hold, backup_seal=seal, executor_sha256="7" * 64, binding_sha256=source.binding_sha256,
                  operation_token=hold.operation_token, run_id=hold.run_id)
    policy = replace(controls.policy, qualification_policy="standalone-cold-restore-v1", automatic_rollback=False)
    return prepared, obs, replace(controls, policy=policy), backup_raw


class RestoreContractTests(unittest.TestCase):
    def test_nonzero_and_zero_plans_preserve_legacy_bootstrap(self):
        for count in (0, 3):
            target, _, proof, raw = restore_release(count)
            validate_manifest(target)
            validate_release_proof(raw, target, repository="future-agi/future-agi")
            self.assertTrue(target["migration"]["requires_bootstrap"])
            self.assertNotIn("state_domains", target["compatibility"])
            self.assertEqual(proof["predecessors"][0]["migration_evidence"]["target_pending_plan_count"], count)

    def test_policy_markers_closed_and_obsolete_predicates_rejected(self):
        target, _, _, _ = restore_release()
        for key, value in (("qualification_policy", "wrong"), ("runtime_activation_contract", "standalone-unpublished-trial-v1"),
                           ("host_observation_contract", "standalone-runtime-v1"), ("backup_inventory_version", 1),
                           ("state_domains", []), ("automatic_rollback", {"strategy": "images_only"})):
            bad = copy.deepcopy(target)
            bad["compatibility"][key] = value
            with self.subTest(key=key), self.assertRaises(ManifestError):
                validate_manifest(bad)
        for field in ("rollback_certified", "from_epoch", "backward_read", "backward_write"):
            bad = copy.deepcopy(target)
            bad["compatibility"]["predecessors"][0][field] = True
            with self.subTest(field=field), self.assertRaises(ManifestError):
                validate_manifest(bad)

    def test_operator_initialization_cannot_be_waived(self):
        target, _, _, _ = restore_release()
        target["compatibility"]["requires_operator_initialization"] = True
        target["compatibility"]["validation"]["inputs_hash"] = inputs_hash(target)
        with self.assertRaises(ManifestError):
            validate_manifest(target)

    def test_v2_hash_binds_migration_and_omits_only_validation(self):
        target, _, _, _ = restore_release()
        digest = inputs_hash(target)
        target["compatibility"]["validation"]["sha256"] = "f" * 64
        self.assertEqual(digest, inputs_hash(target))
        target["migration"]["requires_bootstrap"] = False
        self.assertNotEqual(digest, inputs_hash(target))

    def test_every_row_has_complete_suite_and_migration_evidence(self):
        for field in ("migration_evidence", "executor_sha256", "evidence_inventory_sha256", "persistent_coverage", "execution_deployment"):
            target, _, proof, _ = restore_release()
            del proof["predecessors"][0][field]
            raw = bind_proof(target, proof)
            with self.subTest(field=field), self.assertRaises(ManifestError):
                validate_release_proof(raw, target, repository="future-agi/future-agi")
        for suite in RESTORE_SUITES:
            for result in ("skipped", "unknown", "failed"):
                target, _, proof, _ = restore_release()
                proof["predecessors"][0]["suites"][suite] = result
                raw = bind_proof(target, proof)
                with self.subTest(suite=suite, result=result), self.assertRaises(ManifestError):
                    validate_release_proof(raw, target, repository="future-agi/future-agi")

    def test_migration_plan_hash_count_and_handling_are_typed_and_required(self):
        for field, value in (("target_pending_plan_count", -1), ("target_pending_plan_count", True),
                             ("target_pending_plan_sha256", "unknown"), ("migration_handling", "operator_command"),
                             ("source_inventory_sha256", None)):
            target, _, proof, _ = restore_release()
            proof["predecessors"][0]["migration_evidence"][field] = value
            raw = bind_proof(target, proof)
            with self.subTest(field=field, value=value), self.assertRaises(ManifestError):
                validate_release_proof(raw, target, repository="future-agi/future-agi")

    @needs_jsonschema
    def test_transfer_pathname_counts_are_paired_in_schema_and_runtime(self):
        from jsonschema import Draft202012Validator
        validator = Draft202012Validator(schema("compatibility-proof.schema.json"))
        for backup, restore in ((True, True), (False, False), (True, False), (False, True)):
            target, _, proof, _ = restore_release()
            row = proof["predecessors"][0]
            for present, field in ((backup, "backup_measurement"), (restore, "restore_measurement")):
                if not present:
                    del row[field]["reference_volume_members"]
            with self.subTest(backup=backup, restore=restore):
                self.assertEqual(validator.is_valid(proof), backup == restore)
                if backup == restore:
                    validate_release_proof(bind_proof(target, proof), target, repository="future-agi/future-agi")
                else:
                    with self.assertRaises(ManifestError):
                        validate_release_proof(bind_proof(target, proof), target, repository="future-agi/future-agi")

    def test_v2_schema_runtime_parity_for_every_closed_object(self):
        from jsonschema import Draft202012Validator
        target, _, proof, _ = restore_release()
        backup = json.loads(restore_context()[3])
        for name, value in (("release-manifest.schema.json", target), ("compatibility-proof.schema.json", proof),
                            ("backup-proof.schema.json", backup)):
            spec = schema(name)
            Draft202012Validator.check_schema(spec)
            validator = Draft202012Validator(spec)
            validator.validate(value)
            if name != "release-manifest.schema.json":
                shape(value, spec)
            for key in value:
                bad = copy.deepcopy(value)
                del bad[key]
                if key == "release_notes":
                    continue
                self.assertFalse(validator.is_valid(bad), (name, key))
                with self.assertRaises(ManifestError):
                    validate_manifest(bad) if name == "release-manifest.schema.json" else shape(bad, spec)

    def test_backup_v1_shape_stays_readable_but_not_v2_authority(self):
        from deploy.update.backup_proof import BackupExpectation, validate_backup_proof
        prepared, obs, controls, backup = synthetic_context()
        expected = BackupExpectation(obs.attempt_id, obs.installation_fingerprint, manifest_hash(json.loads(prepared.raw_manifest)),
                                     json.loads(obs.predecessor_raw)["version"], obs.reserved_at,
                                     topology_profile("standalone", ()).backup_domains)
        validate_backup_proof(backup, expected, now=NOW)
        prepared, obs, controls, _ = restore_context()
        value = json.loads(backup)
        value["target_manifest_hash"] = manifest_hash(json.loads(prepared.raw_manifest))
        self.assertFalse(evaluate_automatic_apply(prepared, obs, controls, canonical_json(value)).allowed)


class RestoreAdmissionTests(unittest.TestCase):
    def test_positive_restore_is_mandatory_even_with_rollback_false(self):
        for count in (0, 3):
            prepared, obs, controls, backup = restore_context(count)
            self.assertFalse(controls.policy.automatic_rollback)
            result = evaluate_automatic_apply(prepared, obs, controls, backup)
            self.assertTrue(result.allowed, result.reason_codes)
            self.assertIsInstance(result.transition, AuthorisedTransition)
            self.assertEqual(result.transition.migration_evidence.target_pending_plan_count, count)

    def test_legacy_readable_is_never_automatic(self):
        prepared, obs, controls, backup = restore_context()
        for target in (valid_manifest(), synthetic_release()[0], default_v2()):
            result = evaluate_automatic_apply(prepare(target), obs, controls, backup)
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_NOT_CERTIFIED,))

    def test_pending_only_before_backup_and_cold_hold_needed_afterwards(self):
        prepared, obs, controls, backup = restore_context()
        for boundary in ("before_backup", "after_backup", "before_env_commit", "before_rollout", "unknown"):
            result = evaluate_automatic_apply(prepared, replace(obs, boundary=boundary), controls, "pending")
            self.assertEqual(result.allowed, boundary == "before_backup", result.reason_codes)
            self.assertEqual(result.backup_pending, boundary == "before_backup")
        self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, cold_hold=None), controls, backup).allowed)
        self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, backup_seal=None), controls, backup).allowed)

    def test_no_epoch_or_target_no_migrations_gate(self):
        prepared, obs, controls, backup = restore_context()
        obs = replace(obs, state_epochs={}, state_unchanged=False, no_pending_migrations=False)
        self.assertTrue(evaluate_automatic_apply(prepared, obs, controls, backup).allowed)

    def test_source_migrations_compare_to_source_not_target(self):
        prepared, obs, controls, backup = restore_context()
        for field, value in (("migration_inventory_sha256", "2" * 64), ("applied_migrations_sha256", "4" * 64),
                             ("active_app_mapping_sha256", "a" * 64)):
            source = replace(obs.runtime_observation, **{field: value})
            candidate = replace(obs, runtime_observation=source, boundary="before_backup")
            result = evaluate_automatic_apply(prepared, candidate, controls, "pending")
            self.assertEqual(result.reason_codes, (ReasonCode.MIGRATION_INVENTORY_MISMATCH,))

    def test_unknown_missing_and_changed_inputs_refuse(self):
        prepared, obs, controls, backup = restore_context()
        for field, value in (("runtime_observation", None), ("executor_sha256", "f" * 64), ("release_proof", b""),
                             ("platform", "linux/arm64"), ("deployment_files", {}), ("no_pending_transaction", False),
                             ("predecessor_raw", obs.predecessor_raw + b"\n"), ("binding_sha256", "f" * 64),
                             ("baseline_attested", False), ("owner_confirmed", False), ("topology_qualified", False),
                             ("online_verified", False), ("graph_unchanged", False), ("env_unchanged", False),
                             ("runtime_complete", False), ("no_failed_target", False)):
            with self.subTest(field=field):
                self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, **{field: value}), controls, backup).allowed)
        result = evaluate_automatic_apply(prepared, replace(obs, no_pending_transaction=False), controls, backup)
        self.assertEqual(result.reason_codes, (ReasonCode.AUTOMATIC_RECOVERY_PENDING,))

    def test_effective_controls_and_signatures_still_required(self):
        prepared, obs, controls, backup = restore_context()
        for key, value in (("enabled", False), ("paused", True), ("registered", False), ("window_open", False),
                           ("start_budget_available", False)):
            self.assertFalse(evaluate_automatic_apply(prepared, obs, replace(controls, **{key: value}), backup).allowed)
        for change in ({"verified": False}, {"signer": "wrong"}, {"issuer": "wrong"},
                       {"checked_at": NOW - timedelta(seconds=301)}):
            bad = replace(prepared, verification=replace(prepared.verification, **change))
            self.assertFalse(evaluate_automatic_apply(bad, obs, controls, backup).allowed)
        self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, predecessor_verification=None), controls, backup).allowed)

    def test_no_missing_platform_or_layout_row_can_borrow_evidence(self):
        prepared, obs, controls, backup = restore_context()
        for field, value, reason in (("platform", "linux/arm64", ReasonCode.PLATFORM_NOT_REHEARSED),
                                      ("deployment_files", {}, ReasonCode.DEPLOYMENT_FILES_MISMATCH)):
            result = evaluate_automatic_apply(prepared, replace(obs, **{field: value}), controls, backup)
            self.assertEqual(result.reason_codes, (reason,))

    def test_seal_binds_every_backup_byte_and_hold_identity(self):
        prepared, obs, controls, backup = restore_context()
        for key, value in (("inventory_sha256", "c" * 64), ("source_observation_sha256", "c" * 64),
                           ("binding_sha256", "c" * 64), ("quiescence_evidence_sha256", "c" * 64),
                           ("covered_domains", ["postgres_app"]), ("inventory_version", 1),
                           ("restore_procedure_id", "other")):
            bad = json.loads(backup)
            bad[key] = value
            with self.subTest(key=key):
                self.assertFalse(evaluate_automatic_apply(prepared, obs, controls, canonical_json(bad)).allowed)
        self.assertFalse(evaluate_automatic_apply(prepared, obs, controls, backup + b"\n").allowed)
        hold = replace(obs.cold_hold, operation_token="wrong")
        self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, cold_hold=hold), controls, backup).allowed)

    def test_stale_snapshot_refuses_forward_but_seal_has_no_recovery_expiry(self):
        prepared, obs, controls, backup = restore_context()
        late = NOW + timedelta(hours=2)
        # Shape/identity seal remains valid for recovery. Forward freshness is
        # independently enforced and no new snapshot timestamp is manufactured.
        self.assertEqual(obs.backup_seal.snapshot_at, obs.cold_hold.snapshot_at)
        candidate = replace(obs, now=late, cold_hold=replace(obs.cold_hold, verified_at=late))
        refreshed = replace(prepared, verification=replace(prepared.verification, checked_at=late))
        self.assertEqual(evaluate_automatic_apply(refreshed, candidate, controls, backup).reason_codes,
                         (ReasonCode.BACKUP_EXPIRED,))

    def test_restart_policy_rejection_uses_unsupported_installation(self):
        _, obs, _, _ = restore_context()
        for policy in ("always", "on-failure", "on-failure:3"):
            with self.assertRaises(ContractError) as caught:
                replace(obs.runtime_observation, restart_policies={"9" * 64: policy})
            self.assertEqual(caught.exception.reason_code, "unsupported_installation")
        replace(obs.runtime_observation, restart_policies={"9" * 64: "no"})

    def test_private_interfaces_defensively_freeze_and_reject_bad_values(self):
        import dataclasses
        prepared, obs, controls, backup = restore_context()
        result = evaluate_automatic_apply(prepared, obs, controls, backup)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.transition.fingerprint = "f" * 64
        with self.assertRaises(TypeError):
            obs.runtime_observation.restart_counts["9" * 64] = 1
        with self.assertRaises(TypeError):
            result.transition.execution_deployment["files"]["x"] = "f" * 64
        counts = {"9" * 64: 0}
        runtime = replace(obs.runtime_observation, restart_counts=counts)
        counts["9" * 64] = 1
        self.assertEqual(runtime.restart_counts["9" * 64], 0)
        with self.assertRaises(ContractError):
            replace(obs.backup_seal, inventory_version=True)
        with self.assertRaises(ContractError):
            RestorationResult(obs.attempt_id, "restored", "v1.51.0", "v1.52.0", "restored")
        RestorationResult(obs.attempt_id, "restored", "v1.51.0", "v1.52.0", "restored", obs.backup_seal,
                          obs.runtime_observation, (ReasonCode.RESTORED_AFTER_UPDATE_FAILURE,))

    def test_image_ids_digests_replicas_and_dependencies_remain_exact(self):
        prepared, obs, controls, _ = restore_context()
        for field in ("product_images", "dependency_images"):
            original = getattr(obs.runtime_observation, field)
            service = next(iter(original))
            for change in ("digest", "id", "image", "missing", "extra"):
                images = dict(original)
                if change == "missing":
                    del images[service]
                elif change == "extra":
                    images["unexpected"] = images[service]
                else:
                    key, value = {"digest": ("digest", "sha256:" + "f" * 64),
                                  "id": ("image_id", "unknown"), "image": ("image", "attacker/fork:1")}[change]
                    images[service] = images[service] + (replace(images[service][0], **{key: value}),)
                # Empty complete maps are structurally invalid, as well as ineligible.
                if not images:
                    with self.assertRaises(ContractError):
                        replace(obs.runtime_observation, **{field: images})
                    continue
                runtime = replace(obs.runtime_observation, **{field: images})
                candidate = replace(obs, runtime_observation=runtime, boundary="before_backup")
                with self.subTest(field=field, change=change):
                    self.assertFalse(evaluate_automatic_apply(prepared, candidate, controls, "pending").allowed)

    def test_current_runtime_readiness_and_restart_counts_cannot_be_assumed(self):
        prepared, obs, controls, backup = restore_context()
        for field in ("ready", "stable"):
            runtime = replace(obs.runtime_observation, **{field: False})
            self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, runtime_observation=runtime), controls, backup).allowed)
        hold = replace(obs.cold_hold, restart_counts={"9" * 64: 1})
        self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, cold_hold=hold), controls, backup).allowed)
        # This value predates snapshot_at and is structurally invalid before admission.
        with self.assertRaises(ContractError):
            replace(obs.cold_hold, verified_at=NOW - timedelta(seconds=301))

    def test_fingerprint_is_order_independent_but_binds_actual_source_image_ids(self):
        prepared, obs, controls, _ = restore_context()
        first = obs.runtime_observation.product_images["app"][0]
        second = replace(first, image_id="sha256:" + "f" * 64)
        fingerprints = []
        for replicas in ((first, second), (second, first), (first,)):
            runtime = replace(obs.runtime_observation, product_images={"app": replicas})
            result = evaluate_automatic_apply(prepared, replace(obs, runtime_observation=runtime, boundary="before_backup"), controls, "pending")
            self.assertTrue(result.allowed, result.reason_codes)
            fingerprints.append(result.fingerprint)
        self.assertEqual(fingerprints[0], fingerprints[1])
        self.assertNotEqual(fingerprints[0], fingerprints[2])

    def test_image_only_compatibility_import_is_inert(self):
        from deploy.update.compatibility import evaluate_automatic_rollback
        _, obs, controls, _ = restore_context()
        self.assertFalse(evaluate_automatic_rollback(None, obs, controls).allowed)


class RestoreBudgetTests(unittest.TestCase):
    def test_one_target_startup_and_protected_recovery_reserve(self):
        policy = AutomationPolicy(mode="apply", qualification_policy="standalone-cold-restore-v1")
        forward = policy.backup_timeout_seconds + policy.migration_timeout_seconds + policy.wait_timeout_seconds + policy.stability_period_seconds + 600
        recovery = policy.restore_timeout_seconds + policy.recovery_startup_timeout_seconds + policy.stability_period_seconds + 600
        self.assertEqual(policy.required_forward_budget_seconds, forward)
        self.assertEqual(policy.required_recovery_budget_seconds, recovery)
        self.assertEqual(policy.required_cold_budget_seconds, forward + recovery)
        self.assertEqual(policy.required_tick_budget_seconds, policy.pull_timeout_seconds + forward + recovery)
        self.assertEqual(replace(policy, automatic_rollback=True).required_tick_budget_seconds, policy.required_tick_budget_seconds)
        threshold = policy.required_tick_budget_seconds
        replace(policy, tick_timeout_seconds=threshold)
        with self.assertRaises(ContractError):
            replace(policy, tick_timeout_seconds=threshold - 1)

    def test_native_long_startups_and_independent_restore_budget(self):
        policy = AutomationPolicy(mode="apply", qualification_policy="standalone-cold-restore-v1",
                                  wait_timeout_seconds=4200, recovery_startup_timeout_seconds=4200,
                                  restore_timeout_seconds=7200, tick_timeout_seconds=30000)
        self.assertEqual(policy.required_tick_budget_seconds, 23520)
        self.assertEqual(replace(policy, restore_timeout_seconds=7201).required_tick_budget_seconds, 23521)
        self.assertEqual(replace(policy, wait_timeout_seconds=4201).required_tick_budget_seconds, 23521)


def a6_context():
    # Independent tables at their measured reference inputs give a positive
    # workload. Same-table chains and above-reference hosts are tested below.
    target, _, proof, _ = restore_release()
    row = proof["predecessors"][0]
    for i, migration in enumerate(row["migration_measurements"]):
        migration["tables"][0]["table"] += "_%d" % i if i else ""
    measured = transition_measurements(row)
    host = HostSizeObservation(tuple(HostTableSize(m.engine, m.tables[0].table, 100, 1000)
        for m in measured.migration_measurements),
        {name: 1000 for name in AUTOMATIC_VOLUMES}, 10**12, 10**12, False,
        {name: 1000 for name in AUTOMATIC_VOLUMES}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
    policy = AutomationPolicy(mode="apply", qualification_policy="standalone-cold-restore-v1")
    return measured, host, policy


class AmendmentA6Tests(unittest.TestCase):
    def estimate(self, measured=None, host=None, policy=None, window=14400, tick=14400):
        defaults = a6_context()
        return estimate_automatic_update(measured or defaults[0], host or defaults[1], policy or defaults[2],
                                         window_remaining_seconds=window, tick_remaining_seconds=tick)

    def test_review_probe_large_unmeasured_plan_cannot_be_admitted(self):
        prepared, obs, controls, backup = restore_context(100000)
        self.assertFalse(evaluate_automatic_apply(prepared, obs, controls, backup).allowed)
        prepared, obs, controls, backup = restore_context()
        admitted = evaluate_automatic_apply(prepared, obs, controls, backup)
        self.assertTrue(admitted.allowed)
        self.assertIsInstance(admitted.transition.measurements, TransitionMeasurements)

    def test_all_six_reason_codes_are_shared_event_vocabulary(self):
        for reason in ("migration_exceeds_window", "insufficient_disk_for_migration", "backup_exceeds_window",
                       "insufficient_disk_for_backup", "migration_stalled", "disk_watermark_abort"):
            self.assertEqual(ReasonCode(reason).name, reason.upper())
            event = OperationEvent("blocked", "apply", "planning", NOW, "synthetic-token", (reason,))
            self.assertEqual(event.reason_codes, (ReasonCode(reason),))

    def test_exact_measured_time_growth_and_protected_recovery_arithmetic(self):
        measured, host, policy = a6_context()
        result = self.estimate(measured, host, policy)
        self.assertTrue(result.allowed, result.reason_codes)
        e = result.estimate
        self.assertEqual((e.backup_seconds, e.migration_seconds, e.restore_seconds, e.extra_disk_bytes), (6, 30, 6, 6900))
        self.assertEqual(e.protected_restore_reserve_bytes, 6000 + policy.disk_watermark_reserve_bytes)
        self.assertEqual(e.backup_required_free_bytes, e.protected_restore_reserve_bytes)
        self.assertEqual(e.docker_required_free_bytes, e.protected_restore_reserve_bytes + 6900)
        self.assertEqual((e.forward_seconds, e.recovery_seconds, e.total_seconds), (1896, 1866, 3762))
        self.assertEqual(e.migration_deadline_seconds, 30)
        self.assertEqual(e.migration_stall_timeout_seconds, policy.migration_stall_timeout_seconds)

    def test_each_actual_table_dimension_above_reference_refuses_without_a_scaling_bound(self):
        measured, host, _ = a6_context()
        # A larger row or byte dimension is unsupported, not a linear workload
        # certified by a risk class. Preserve all three original growth cases.
        for dimensions in ({"rows": 200, "bytes": 2000}, {"rows": 200}, {"bytes": 2000}):
            larger = replace(host, table_sizes=tuple(replace(t, **dimensions) for t in host.table_sizes))
            with self.subTest(dimensions=dimensions):
                result = self.estimate(measured, larger)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
                self.assertIsNone(result.estimate)

    def test_window_and_remaining_tick_include_the_full_restore_reserve(self):
        total = self.estimate().estimate.total_seconds
        self.assertTrue(self.estimate(window=total, tick=total).allowed)
        for budgets in ({"window": total - 1}, {"tick": total - 1}):
            result = self.estimate(**budgets)
            self.assertIn(ReasonCode.MIGRATION_EXCEEDS_WINDOW, result.reason_codes)
        result = self.estimate(window=total - 31)
        self.assertIn(ReasonCode.BACKUP_EXCEEDS_WINDOW, result.reason_codes)
        self.assertIn(ReasonCode.MIGRATION_EXCEEDS_WINDOW, result.reason_codes)

    def test_configured_migration_backup_and_restore_deadlines_cannot_be_borrowed(self):
        measured, host, policy = a6_context()
        result = self.estimate(policy=replace(policy, migration_timeout_seconds=29, migration_stall_timeout_seconds=10))
        self.assertIn(ReasonCode.MIGRATION_EXCEEDS_WINDOW, result.reason_codes)
        for field in ("backup_measurement", "restore_measurement"):
            slower = replace(measured, **{field: replace(getattr(measured, field), throughput_bytes_per_second=1)})
            self.assertIn(ReasonCode.BACKUP_EXCEEDS_WINDOW, self.estimate(slower, host).reason_codes)

    def test_each_filesystem_is_admitted_independently_at_exact_byte_threshold(self):
        measured, host, _ = a6_context()
        e = self.estimate().estimate
        fit = replace(host, docker_free_bytes=e.docker_required_free_bytes, backup_free_bytes=e.backup_required_free_bytes)
        self.assertTrue(self.estimate(measured, fit).allowed)
        self.assertEqual(self.estimate(measured, replace(fit, backup_free_bytes=fit.backup_free_bytes - 1)).reason_codes,
                         (ReasonCode.INSUFFICIENT_DISK_FOR_BACKUP,))
        self.assertEqual(self.estimate(measured, replace(fit, docker_free_bytes=fit.docker_free_bytes - 1)).reason_codes,
                         (ReasonCode.INSUFFICIENT_DISK_FOR_MIGRATION,))
        reasons = self.estimate(measured, replace(fit, docker_free_bytes=e.protected_restore_reserve_bytes - 1)).reason_codes
        self.assertIn(ReasonCode.INSUFFICIENT_DISK_FOR_BACKUP, reasons)
        self.assertIn(ReasonCode.INSUFFICIENT_DISK_FOR_MIGRATION, reasons)

    def test_colocated_or_unknown_backup_filesystem_accounts_for_both_consumers(self):
        measured, host, _ = a6_context()
        separate = self.estimate(measured, host).estimate
        same = self.estimate(measured, replace(host, backup_shares_docker_filesystem=True)).estimate
        self.assertEqual(same.docker_required_free_bytes, separate.docker_required_free_bytes + same.backup_required_free_bytes)
        unknown = HostSizeObservation(host.table_sizes, host.volume_bytes, host.docker_free_bytes, host.backup_free_bytes,
                                      transfer_bytes=host.transfer_bytes, transfer_members=host.transfer_members)
        self.assertEqual(self.estimate(measured, unknown).estimate, same)

    def test_missing_table_unknown_engine_and_unmeasured_reference_capacity_refuse(self):
        measured, host, _ = a6_context()
        for tables in ((), (HostTableSize("clickhouse", "public.synthetic_table", 100, 1000),)):
            self.assertEqual(self.estimate(measured, replace(host, table_sizes=tables)).reason_codes,
                             (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
        m = measured.migration_measurements[0]
        no_reference = replace(m, tables=(replace(m.tables[0], reference_rows=0),))
        candidate = replace(measured, migration_measurements=(no_reference,) + measured.migration_measurements[1:])
        self.assertEqual(self.estimate(candidate, host).reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
        sizes = dict(measured.backup_measurement.reference_volume_bytes)
        sizes["app-data"] = 0
        transfer = replace(measured.backup_measurement, reference_volume_bytes=sizes)
        candidate = replace(measured, backup_measurement=transfer, restore_measurement=transfer)
        self.assertEqual(self.estimate(candidate, host).reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))

    def test_both_engines_risks_and_throughput_units_have_positive_contract_examples(self):
        from deploy.update.contracts import MigrationMeasurement
        target, _, proof, _ = restore_release(1)
        for engine in ("postgres", "clickhouse"):
            for risk in ("metadata_only", "scan", "rewrite", "index_build", "data_backfill", "other"):
                for unit in ("rows_per_second", "bytes_per_second"):
                    row = copy.deepcopy(proof["predecessors"][0])
                    row["migration_measurements"][0].update(engine=engine, risk_class=risk)
                    row["migration_measurements"][0]["throughput"]["unit"] = unit
                    measured = transition_measurements(row)
                    self.assertIsInstance(measured.migration_measurements[0], MigrationMeasurement)
                    host = replace(a6_context()[1], table_sizes=(HostTableSize(engine, "public.synthetic_table", 100, 1000),))
                    self.assertTrue(self.estimate(measured, host).allowed)

    def test_no_table_metadata_operation_still_budgets_measured_duration_wal_and_temp(self):
        target, _, proof, _ = restore_release(1)
        proof["predecessors"][0]["migration_measurements"][0].update(tables=[], risk_class="metadata_only")
        measured = transition_measurements(proof["predecessors"][0])
        result = self.estimate(measured, replace(a6_context()[1], table_sizes=()))
        self.assertTrue(result.allowed)
        self.assertEqual((result.estimate.migration_seconds, result.estimate.extra_disk_bytes), (10, 300))

    def test_zero_pending_plan_requires_measured_backup_and_restore(self):
        target, _, proof, raw = restore_release(0)
        measured = transition_measurements(proof["predecessors"][0])
        self.assertEqual(self.estimate(measured).estimate.migration_seconds, 0)
        for field in ("backup_measurement", "restore_measurement"):
            bad = copy.deepcopy(proof)
            del bad["predecessors"][0][field]
            raw = bind_proof(target, bad)
            with self.assertRaises(ValueError):
                validate_release_proof(raw, target, repository="future-agi/future-agi")

    def test_exact_plan_count_identity_order_and_hash_are_all_bound(self):
        for change in ("missing", "extra", "duplicate", "reordered", "renamed", "hash", "count"):
            target, _, proof, _ = restore_release()
            row = proof["predecessors"][0]
            migrations = row["migration_measurements"]
            if change == "missing": migrations.pop()
            elif change == "extra": migrations.append(copy.deepcopy(migrations[0]))
            elif change == "duplicate": migrations[1] = copy.deepcopy(migrations[0])
            elif change == "reordered": migrations.reverse()
            elif change == "renamed": migrations[0]["name"] = "different"
            elif change == "hash": row["migration_evidence"]["target_pending_plan_sha256"] = "f" * 64
            else: row["migration_evidence"]["target_pending_plan_count"] += 1
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_release_proof(bind_proof(target, proof), target, repository="future-agi/future-agi")

    def test_measurements_are_covered_by_the_signed_exact_proof_bytes(self):
        target, _, proof, raw = restore_release()
        proof["predecessors"][0]["migration_measurements"][0]["duration_seconds"] += 1
        with self.assertRaises(ValueError):
            validate_release_proof(canonical_json(proof), target, repository="future-agi/future-agi")
        validate_release_proof(raw, target, repository="future-agi/future-agi")

    def test_measurement_inputs_are_deeply_immutable_and_defensively_copied(self):
        target, _, proof, raw = restore_release()
        row = proof["predecessors"][0]
        measured = transition_measurements(row)
        row["backup_measurement"]["reference_volume_bytes"]["app-data"] = 1
        row["migration_measurements"][0]["tables"][0]["reference_rows"] = 1
        self.assertEqual(measured.backup_measurement.reference_volume_bytes["app-data"], 1000)
        self.assertEqual(measured.migration_measurements[0].tables[0].reference_rows, 100)
        with self.assertRaises(TypeError): measured.restore_measurement.reference_volume_bytes["app-data"] = 1
        measured, host, _ = a6_context()
        values = dict(host.volume_bytes)
        copied = replace(host, volume_bytes=values)
        values["app-data"] = 1
        self.assertEqual(copied.volume_bytes["app-data"], 1000)

    def test_every_new_policy_bound_and_legacy_budgets(self):
        policy = a6_context()[2]
        for field in ("estimate_safety_factor", "migration_timeout_seconds", "migration_stall_timeout_seconds", "disk_watermark_reserve_bytes"):
            lo, hi = AUTOMATIC_POLICY_BOUNDS[field]
            for value in (True, float("nan"), float("inf"), lo - 1, hi + 1):
                with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                    replace(policy, **{field: value})
        with self.assertRaises(ContractError): replace(policy, migration_stall_timeout_seconds=1201)
        self.assertEqual(replace(policy, migration_timeout_seconds=1201).required_tick_budget_seconds,
                         policy.required_tick_budget_seconds + 1)
        legacy = AutomationPolicy(mode="apply")
        self.assertEqual(replace(legacy, migration_timeout_seconds=7200).required_tick_budget_seconds,
                         legacy.required_tick_budget_seconds)
        self.assertEqual(replace(policy, estimate_safety_factor=3).required_tick_budget_seconds, policy.required_tick_budget_seconds)
        self.assertEqual(self.estimate(policy=replace(policy, estimate_safety_factor=3)).estimate.migration_seconds, 45)

    def test_missing_and_nonfinite_host_sizes_and_remaining_budgets_refuse(self):
        measured, host, policy = a6_context()
        self.assertFalse(estimate_automatic_update(measured, None, policy, window_remaining_seconds=10, tick_remaining_seconds=10).allowed)
        for field in ("docker_free_bytes", "backup_free_bytes"):
            for value in (None, True, -1, float("inf"), float("nan"), 1.5):
                with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                    replace(host, **{field: value})
        for field in ("rows", "bytes"):
            with self.assertRaises(ContractError): replace(host.table_sizes[0], **{field: float("nan")})
        for value in (None, True, -1, float("inf"), float("nan")):
            self.assertFalse(self.estimate(window=value).allowed)
            self.assertFalse(self.estimate(tick=value).allowed)
        for missing in AUTOMATIC_VOLUMES:
            with self.assertRaises(ContractError):
                replace(host, volume_bytes={k: v for k, v in host.volume_bytes.items() if k != missing})

    def test_fractional_rounding_and_extreme_finite_rates_never_overflow_or_round_down(self):
        measured, host, _ = a6_context()
        transfer = replace(measured.backup_measurement, throughput_bytes_per_second=4000)
        measured = replace(measured, backup_measurement=transfer, restore_measurement=transfer)
        self.assertEqual(self.estimate(measured, host).estimate.backup_seconds, 2)
        transfer = replace(transfer, throughput_bytes_per_second=5e-324)
        measured = replace(measured, backup_measurement=transfer, restore_measurement=transfer)
        result = self.estimate(measured, host)
        self.assertFalse(result.allowed)
        self.assertIsInstance(result.estimate.backup_seconds, int)
        self.assertIn(ReasonCode.BACKUP_EXCEEDS_WINDOW, result.reason_codes)


class TransferBasisA6Tests(unittest.TestCase):
    """Transfer time compares the signed sealed-tree basis with the host's.

    Native: a fresh installation whose restore workload (logical + 8 KiB per
    member) was ~31x a small role's logical payload estimated 13,482 s to
    restore data the qualification restored in ~215 s, so every run refused.
    Time now scales by host payload AND pathnames against the signed reference;
    space keeps the conservative restore workload.
    """
    estimate = AmendmentA6Tests.estimate
    MEMBERS = {"app-data": 10, "postgres-data": 10, "clickhouse-data": 10}

    def measured(self, members=MEMBERS):
        measured = a6_context()[0]
        return replace(measured, backup_measurement=replace(measured.backup_measurement, reference_volume_members=members),
                       restore_measurement=replace(measured.restore_measurement, reference_volume_members=members))

    def host(self, restore_work, transfer=None, members=None, free=10**12):
        measured = a6_context()[0]
        return HostSizeObservation(tuple(HostTableSize(m.engine, m.tables[0].table, 100, 1000)
            for m in measured.migration_measurements), restore_work, free, free, False, transfer, members)

    def seconds(self, host, members=MEMBERS):
        result = self.estimate(self.measured(members), host)
        return result, (result.estimate.backup_seconds, result.estimate.restore_seconds)

    def test_member_metadata_allowance_does_not_scale_time_for_the_same_trees(self):
        work = {"app-data": 31000, "postgres-data": 1000, "clickhouse-data": 1000}
        result, seconds = self.seconds(self.host(work, {n: 1000 for n in AUTOMATIC_VOLUMES}, self.MEMBERS))
        self.assertTrue(result.allowed, result.reason_codes)
        self.assertEqual(seconds, (6, 6))

    def test_pathname_growth_scales_time_even_with_constant_payload(self):
        # Review finding: 300x more (empty) files with the same payload.
        members = dict(self.MEMBERS, **{"app-data": 3000})
        work = {"app-data": 1000 + 3000 * 8192, "postgres-data": 1000, "clickhouse-data": 1000}
        _, seconds = self.seconds(self.host(work, {n: 1000 for n in AUTOMATIC_VOLUMES}, members))
        self.assertEqual(seconds, (1800, 1800))

    def test_zero_payload_role_still_scales_by_its_pathnames(self):
        measured = self.measured()
        rows = dict(measured.backup_measurement.reference_volume_bytes, **{"app-data": 0})
        transfer = replace(measured.backup_measurement, reference_volume_bytes=rows)
        measured = replace(measured, backup_measurement=transfer, restore_measurement=transfer)
        members = dict(self.MEMBERS, **{"app-data": 500})
        host = self.host({n: 10**6 for n in AUTOMATIC_VOLUMES}, {"app-data": 0, "postgres-data": 1000,
                         "clickhouse-data": 1000}, members)
        self.assertEqual(self.estimate(measured, host).estimate.backup_seconds, 4 * 50)

    def test_logical_growth_still_scales_by_the_largest_role_ratio(self):
        transfer = {"app-data": 5000, "postgres-data": 1000, "clickhouse-data": 1000}
        _, seconds = self.seconds(self.host({n: 9000 for n in AUTOMATIC_VOLUMES}, transfer, self.MEMBERS))
        self.assertEqual(seconds, (30, 30))

    def test_space_still_uses_the_conservative_restore_workload(self):
        policy = a6_context()[2]
        work = {"app-data": 31000, "postgres-data": 1000, "clickhouse-data": 1000}
        transfer = {n: 1000 for n in AUTOMATIC_VOLUMES}
        result, _ = self.seconds(self.host(work, transfer, self.MEMBERS))
        self.assertEqual(result.estimate.backup_required_free_bytes, 2 * 33000 + policy.disk_watermark_reserve_bytes)
        tight = self.host(work, transfer, self.MEMBERS, free=2 * 3000 + policy.disk_watermark_reserve_bytes)
        self.assertIn(ReasonCode.INSUFFICIENT_DISK_FOR_BACKUP, self.estimate(self.measured(), tight).reason_codes)

    def test_missing_pathname_basis_on_either_side_refuses(self):
        # Review: restore workload bytes are not a time bound for pathname
        # growth, so neither side may fall back to them.
        work = {"app-data": 31000, "postgres-data": 1000, "clickhouse-data": 1000}
        transfer = {n: 1000 for n in AUTOMATIC_VOLUMES}
        for host, members in ((self.host(work), self.MEMBERS), (self.host(work, transfer), self.MEMBERS),
                              (self.host(work, transfer, self.MEMBERS), None)):
            with self.subTest(host=host.transfer_members, reference=members):
                result = self.estimate(self.measured(members), host)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))

    def test_bases_must_cover_every_role_and_stay_within_the_workload(self):
        work = {name: 1000 for name in AUTOMATIC_VOLUMES}
        for transfer, members in (({"app-data": 1000}, None), (dict(work, **{"app-data": 1001}), None),
                                  (work, {"app-data": 1}), (work, dict(self.MEMBERS, **{"app-data": 0}))):
            with self.subTest(transfer=transfer, members=members), self.assertRaises(ValueError):
                self.host(work, transfer, members)
        measured = a6_context()[0]
        with self.assertRaises(ValueError):
            replace(measured.backup_measurement, reference_volume_members=dict(self.MEMBERS, **{"app-data": 0}))
        with self.assertRaises(ValueError):
            replace(measured, backup_measurement=replace(measured.backup_measurement,
                    reference_volume_members=self.MEMBERS))


class ReviewerA6RegressionTests(unittest.TestCase):
    """Pure synthetic counterexamples, never product qualification."""

    estimate = AmendmentA6Tests.estimate

    def measured_row(self, target, proof):
        raw = bind_proof(target, proof)
        validate_release_proof(raw, target, repository="future-agi/future-agi")
        from jsonschema import Draft202012Validator
        Draft202012Validator(schema("compatibility-proof.schema.json")).validate(proof)
        return transition_measurements(proof["predecessors"][0])

    def test_reviewer_chained_tenfold_backfills_refuse_unbounded_intermediate_inputs(self):
        target, _, proof, _ = restore_release(2)
        for i, migration in enumerate(proof["predecessors"][0]["migration_measurements"]):
            reference = 1000 * 10**i
            migration.update(tables=[{"table": "public.synthetic_table", "reference_rows": reference // 10,
                                     "reference_bytes": reference}], duration_seconds=reference // 1000,
                throughput={"unit": "bytes_per_second", "value": 1000},
                peak_disk_amplification_factor=10, wal_bytes=0, temp_bytes=0, risk_class="data_backfill")
        measured = self.measured_row(target, proof)
        policy = replace(a6_context()[2], disk_watermark_reserve_bytes=1)
        host = HostSizeObservation((HostTableSize("postgres", "public.synthetic_table", 1000, 10000),),
            {"app-data": 1, "postgres-data": 10000, "clickhouse-data": 1}, 380005, 10**9, False,
            {"app-data": 1, "postgres-data": 10000, "clickhouse-data": 1}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
        for free in (380005, 10**12):
            result = self.estimate(measured, replace(host, docker_free_bytes=free), policy)
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
            self.assertIsNone(result.estimate)

    def test_repeat_tables_refuse_even_with_no_measured_disk_growth_or_nonadjacent_use(self):
        for engine in ("postgres", "clickhouse"):
            for risk in ("metadata_only", "scan", "rewrite", "index_build", "data_backfill", "other"):
                for separation in (0, 1):
                    target, _, proof, _ = restore_release(2 + separation)
                    migrations = proof["predecessors"][0]["migration_measurements"]
                    for migration in migrations:
                        migration.update(engine=engine, risk_class=risk, peak_disk_amplification_factor=1)
                    if separation:
                        migrations[1]["tables"][0]["table"] = "public.unrelated_table"
                    measured = self.measured_row(target, proof)
                    host = HostSizeObservation(tuple(HostTableSize(engine, name, 100, 1000)
                        for name in ("public.synthetic_table", "public.unrelated_table")),
                        {name: 1000 for name in AUTOMATIC_VOLUMES}, 10**12, 10**12, False,
        {name: 1000 for name in AUTOMATIC_VOLUMES}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
                    with self.subTest(engine=engine, risk=risk, separation=separation):
                        result = self.estimate(measured, host)
                        self.assertFalse(result.allowed)
                        self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))

    def test_independent_tables_and_engine_namespaces_remain_estimable(self):
        for same_engine in (True, False):
            target, _, proof, _ = restore_release(2)
            second = proof["predecessors"][0]["migration_measurements"][1]
            if same_engine:
                second["tables"][0]["table"] = "public.independent_table"
            else:
                second["engine"] = "clickhouse"
            measured = self.measured_row(target, proof)
            host = HostSizeObservation(tuple(HostTableSize(m.engine, m.tables[0].table, 100, 1000)
                for m in measured.migration_measurements),
                {name: 1000 for name in AUTOMATIC_VOLUMES}, 10**12, 10**12, False,
        {name: 1000 for name in AUTOMATIC_VOLUMES}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
            result = self.estimate(measured, host)
            self.assertTrue(result.allowed, result.reason_codes)
            self.assertEqual((result.estimate.migration_seconds, result.estimate.extra_disk_bytes), (20, 4600))

    def test_reviewer_row_scaled_index_preserves_restore_reserve(self):
        target, _, proof, _ = restore_release(1)
        proof["predecessors"][0]["migration_measurements"][0].update(
            tables=[{"table": "public.synthetic_table", "reference_rows": 100, "reference_bytes": 100000}],
            duration_seconds=1, throughput={"unit": "rows_per_second", "value": 100},
            peak_disk_amplification_factor=1.1, wal_bytes=0, temp_bytes=0, risk_class="index_build")
        measured = self.measured_row(target, proof)
        host = HostSizeObservation((HostTableSize("postgres", "public.synthetic_table", 1000, 100000),),
            {"app-data": 1, "postgres-data": 100000, "clickhouse-data": 1}, 220006, 10**9, False,
            {"app-data": 1, "postgres-data": 100000, "clickhouse-data": 1}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
        policy = replace(a6_context()[2], disk_watermark_reserve_bytes=1)
        # The current contract has no scaling bound, including for index_build.
        # Refusal is required even if the host offers ample time and disk.
        for free in (220006, 10**12):
            result = self.estimate(measured, replace(host, docker_free_bytes=free), policy)
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
            self.assertIsNone(result.estimate)
        # An independent synthetic reference run at the host row cardinality
        # would measure 100,000 index bytes; test that reference's capacity too.
        migration = proof["predecessors"][0]["migration_measurements"][0]
        migration["tables"][0]["reference_rows"] = 1000
        migration.update(duration_seconds=10, peak_disk_amplification_factor=2)
        measured = self.measured_row(target, proof)
        result = self.estimate(measured, host, policy)
        self.assertFalse(result.allowed)
        self.assertIn(ReasonCode.INSUFFICIENT_DISK_FOR_MIGRATION, result.reason_codes)
        self.assertEqual(result.estimate.extra_disk_bytes, 2 * 100000)
        self.assertEqual(result.estimate.docker_required_free_bytes,
                         result.estimate.protected_restore_reserve_bytes + 2 * 100000)
        fit = replace(host, docker_free_bytes=result.estimate.docker_required_free_bytes)
        self.assertTrue(self.estimate(measured, fit, policy).allowed)
        self.assertFalse(self.estimate(measured, replace(fit, docker_free_bytes=fit.docker_free_bytes - 1), policy).allowed)

    def test_all_allocation_keeps_reference_floor_or_refuses_unbounded_scaling(self):
        target, _, proof, _ = restore_release(1)
        measured = self.measured_row(target, proof)
        original = measured.migration_measurements[0]
        for engine in ("postgres", "clickhouse"):
            for unit in ("rows_per_second", "bytes_per_second"):
                for rows in (1, 100, 1000):
                    for byte_count in (1, 1000, 10000):
                        for amplification in (1, 1.1, 10):
                            migration = replace(original, engine=engine, peak_disk_amplification_factor=amplification,
                                                throughput=replace(original.throughput, unit=unit))
                            candidate = replace(measured, migration_measurements=(migration,))
                            host = HostSizeObservation((HostTableSize(engine, "public.synthetic_table", rows, byte_count),),
                                {name: 1000 for name in AUTOMATIC_VOLUMES}, 10**12, 10**12, False,
        {name: 1000 for name in AUTOMATIC_VOLUMES}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
                            result = self.estimate(candidate, host)
                            with self.subTest(engine=engine, unit=unit, rows=rows, bytes=byte_count, amplification=amplification):
                                if rows > 100 or byte_count > 1000:
                                    self.assertFalse(result.allowed)
                                    self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
                                    self.assertIsNone(result.estimate)
                                else:
                                    required = 2 * ((Fraction(amplification) - 1) * 1000 + 150)
                                    self.assertTrue(result.allowed, result.reason_codes)
                                    self.assertGreaterEqual(result.estimate.extra_disk_bytes, required)
                                    self.assertLess(result.estimate.extra_disk_bytes - required, 1)

    def test_multi_table_allocation_keeps_reference_floor_and_refuses_larger_inputs(self):
        target, _, proof, _ = restore_release(1)
        proof["predecessors"][0]["migration_measurements"][0]["tables"].append(
            {"table": "public.second_table", "reference_rows": 100, "reference_bytes": 5000})
        measured = self.measured_row(target, proof)
        host = HostSizeObservation((HostTableSize("postgres", "public.synthetic_table", 1000, 1000),
                                   HostTableSize("postgres", "public.second_table", 100, 5000)),
            {name: 1000 for name in AUTOMATIC_VOLUMES}, 10**12, 10**12, False,
        {name: 1000 for name in AUTOMATIC_VOLUMES}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
        result = self.estimate(measured, host)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
        self.assertIsNone(result.estimate)
        for rows in (0, 100):
            within_reference = replace(host, table_sizes=(replace(host.table_sizes[0], rows=rows), host.table_sizes[1]))
            result = self.estimate(measured, within_reference)
            self.assertTrue(result.allowed, result.reason_codes)
            self.assertEqual(result.estimate.extra_disk_bytes, 2 * (6000 + 150))

    def test_reviewer_role_mix_transfer_refuses_both_sixty_second_timeouts(self):
        target, _, proof, _ = restore_release(0)
        mib = 1024**2
        reference = {"app-data": mib, "postgres-data": 9998 * mib, "clickhouse-data": mib}
        reference_duration = Fraction(1) + Fraction(9998, 1000) + Fraction(1, 1000)
        rate = float(Fraction(sum(reference.values())) / reference_duration)
        for field in ("backup_measurement", "restore_measurement"):
            proof["predecessors"][0][field] = {"reference_volume_bytes": reference,
                                             "reference_volume_members": {role: 1 for role in AUTOMATIC_VOLUMES},
                                             "throughput_bytes_per_second": rate}
        measured = self.measured_row(target, proof)
        sizes = {"app-data": 9998 * mib, "postgres-data": mib, "clickhouse-data": mib}
        host = HostSizeObservation((), sizes, 10**12, 10**12, False, sizes, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
        policy = replace(a6_context()[2], backup_timeout_seconds=60, restore_timeout_seconds=60)
        result = self.estimate(measured, host, policy)
        actual_duration = Fraction(9998) + Fraction(2, 1000)
        self.assertFalse(result.allowed)
        self.assertIn(ReasonCode.BACKUP_EXCEEDS_WINDOW, result.reason_codes)
        self.assertGreaterEqual(result.estimate.backup_seconds, 2 * actual_duration)
        self.assertGreaterEqual(result.estimate.restore_seconds, 2 * actual_duration)

    def test_transfer_scales_by_each_role_and_keeps_measured_reference_floor(self):
        target, _, proof, _ = restore_release(0)
        measured = self.measured_row(target, proof)
        reference = {"app-data": 2, "postgres-data": 1000, "clickhouse-data": 5}
        transfer = replace(measured.backup_measurement, reference_volume_bytes=reference,
                           throughput_bytes_per_second=100)
        measured = replace(measured, backup_measurement=transfer, restore_measurement=transfer)
        for growing_role in AUTOMATIC_VOLUMES:
            previous = 0
            for multiple in (0, 1, 2, 10, 1000):
                sizes = {role: value if role != growing_role else value * multiple for role, value in reference.items()}
                host = HostSizeObservation((), sizes, 10**12, 10**12, False, sizes, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
                result = self.estimate(measured, host)
                expected = Fraction(2 * sum(reference.values()) * max(1, multiple), 100)
                self.assertGreaterEqual(result.estimate.backup_seconds, expected)
                self.assertLess(result.estimate.backup_seconds - expected, 1)
                self.assertEqual(result.estimate.backup_seconds, result.estimate.restore_seconds)
                self.assertGreaterEqual(result.estimate.backup_seconds, previous)
                previous = result.estimate.backup_seconds

    def test_nonempty_host_role_with_zero_reference_still_refuses(self):
        target, _, proof, _ = restore_release(0)
        measured = self.measured_row(target, proof)
        for missing_role in AUTOMATIC_VOLUMES:
            reference = {role: 0 if role == missing_role else 1000 for role in AUTOMATIC_VOLUMES}
            transfer = replace(measured.backup_measurement, reference_volume_bytes=reference)
            candidate = replace(measured, backup_measurement=transfer, restore_measurement=transfer)
            host = HostSizeObservation((), {role: 1000 for role in AUTOMATIC_VOLUMES}, 10**12, 10**12, False,
                                       {role: 1000 for role in AUTOMATIC_VOLUMES}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
            result = self.estimate(candidate, host)
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
            host = replace(host, volume_bytes=reference, transfer_bytes=reference)
            self.assertTrue(self.estimate(candidate, host).allowed)


class NonlinearA6RegressionTests(unittest.TestCase):
    """Reference measurements are not a certified nonlinear scaling bound."""

    estimate = AmendmentA6Tests.estimate
    measured_row = ReviewerA6RegressionTests.measured_row

    def test_reviewer_cartesian_backfill_refuses_extrapolation_with_disjoint_tables(self):
        target, _, proof, _ = restore_release(1)
        proof["predecessors"][0]["migration_measurements"][0].update(
            risk_class="data_backfill", tables=[
                {"table": "public.left_input", "reference_rows": 100, "reference_bytes": 1000},
                {"table": "public.right_input", "reference_rows": 100, "reference_bytes": 1000},
                {"table": "public.pairs", "reference_rows": 0, "reference_bytes": 0}],
            duration_seconds=10, throughput={"unit": "rows_per_second", "value": 20},
            peak_disk_amplification_factor=51, wal_bytes=0, temp_bytes=0)
        measured = self.measured_row(target, proof)
        host = HostSizeObservation((HostTableSize("postgres", "public.left_input", 1000, 10000),
                                   HostTableSize("postgres", "public.right_input", 1000, 10000),
                                   HostTableSize("postgres", "public.pairs", 0, 0)),
            {"app-data": 1, "postgres-data": 20000, "clickhouse-data": 1}, 2040005, 10**12, False,
            {"app-data": 1, "postgres-data": 20000, "clickhouse-data": 1}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})
        policy = replace(a6_context()[2], disk_watermark_reserve_bytes=1)
        for free in (2040005, 10**12):
            with self.subTest(docker_free_bytes=free):
                result = self.estimate(measured, replace(host, docker_free_bytes=free), policy)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
                self.assertIsNone(result.estimate)

    def test_no_engine_risk_or_throughput_label_certifies_extrapolation(self):
        target, _, proof, _ = restore_release(1)
        measured = self.measured_row(target, proof)
        original = measured.migration_measurements[0]
        for engine in ("postgres", "clickhouse"):
            for risk in ("metadata_only", "scan", "rewrite", "index_build", "data_backfill", "other"):
                for unit in ("rows_per_second", "bytes_per_second"):
                    migration = replace(original, engine=engine, risk_class=risk, peak_disk_amplification_factor=1,
                                        throughput=replace(original.throughput, unit=unit), wal_bytes=0, temp_bytes=0)
                    candidate = replace(measured, migration_measurements=(migration,))
                    for rows, byte_count in ((101, 1000), (100, 1001), (101, 1001), (101, 1), (1, 1001)):
                        host = replace(a6_context()[1], table_sizes=(
                            HostTableSize(engine, "public.synthetic_table", rows, byte_count),))
                        with self.subTest(engine=engine, risk=risk, unit=unit, rows=rows, bytes=byte_count):
                            result = self.estimate(candidate, host)
                            self.assertFalse(result.allowed)
                            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
                            self.assertIsNone(result.estimate)

    def test_equal_aggregate_work_cannot_hide_one_table_above_its_reference(self):
        target, _, proof, _ = restore_release(1)
        proof["predecessors"][0]["migration_measurements"][0]["tables"].append(
            {"table": "public.other_input", "reference_rows": 100, "reference_bytes": 1000})
        measured = self.measured_row(target, proof)
        host = replace(a6_context()[1], table_sizes=(HostTableSize("postgres", "public.synthetic_table", 101, 1000),
                                                  HostTableSize("postgres", "public.other_input", 99, 1000)))
        for tables in (host.table_sizes, tuple(reversed(host.table_sizes))):
            result = self.estimate(measured, replace(host, table_sizes=tables))
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
            self.assertIsNone(result.estimate)

    def test_equal_and_smaller_inputs_keep_full_reference_time_and_allocation(self):
        target, _, proof, _ = restore_release(1)
        measured = self.measured_row(target, proof)
        for rows, byte_count in ((100, 1000), (99, 999), (0, 0), (1, 1000), (100, 1)):
            host = replace(a6_context()[1], table_sizes=(HostTableSize("postgres", "public.synthetic_table", rows, byte_count),))
            with self.subTest(rows=rows, bytes=byte_count):
                result = self.estimate(measured, host)
                self.assertTrue(result.allowed, result.reason_codes)
                self.assertEqual(result.estimate.migration_seconds, 10)
                self.assertEqual(result.estimate.extra_disk_bytes, 2300)

    def test_reference_comparison_is_exact_at_largest_supported_integer(self):
        target, _, proof, _ = restore_release(1)
        measured = self.measured_row(target, proof)
        original = measured.migration_measurements[0]
        maximum = 9007199254740991
        for field, host_field in (("reference_rows", "rows"), ("reference_bytes", "bytes")):
            table = replace(original.tables[0], **{field: maximum - 1})
            migration = replace(original, tables=(table,), peak_disk_amplification_factor=1, wal_bytes=0, temp_bytes=0,
                                throughput=replace(original.throughput, value=1e18))
            candidate = replace(measured, migration_measurements=(migration,))
            actual = HostTableSize("postgres", table.table, table.reference_rows, table.reference_bytes)
            host = replace(a6_context()[1], table_sizes=(actual,))
            self.assertTrue(self.estimate(candidate, host).allowed)
            result = self.estimate(candidate, replace(host, table_sizes=(replace(actual, **{host_field: maximum}),)))
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
            self.assertIsNone(result.estimate)

    def test_unrelated_observed_tables_do_not_require_migration_reference_coverage(self):
        measured, host, _ = a6_context()
        larger_unrelated = HostTableSize("postgres", "public.not_in_pending_plan", 9007199254740991, 9007199254740991)
        result = self.estimate(measured, replace(host, table_sizes=host.table_sizes + (larger_unrelated,)))
        self.assertTrue(result.allowed, result.reason_codes)
        self.assertEqual(result.estimate, self.estimate(measured, host).estimate)

    def test_larger_safety_factor_does_not_authorize_unbounded_extrapolation(self):
        measured, host, policy = a6_context()
        host = replace(host, table_sizes=(replace(host.table_sizes[0], rows=101),) + host.table_sizes[1:])
        for factor in (2, 100):
            result = self.estimate(measured, host, replace(policy, estimate_safety_factor=factor))
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
            self.assertIsNone(result.estimate)


class ReviewParityTests(unittest.TestCase):
    def test_mathematical_integer_parity_does_not_change_legacy_manual_or_backup_tokens(self):
        # A6 is unreleased v2 only; do not broaden existing backup-v1/manual parsing.
        backup = json.loads(synthetic_context()[3])
        backup["schema_version"] = 1.0
        with self.assertRaises(CompatibilityError):
            shape(backup, schema("backup-proof.schema.json"))
        target, _, proof, _ = synthetic_release()
        proof["run_id"] = 42.0
        with self.assertRaises(CompatibilityError):
            validate_release_proof(bind_proof(target, proof), target, repository="future-agi/future-agi")

    def test_integer_valued_float_counts_run_ids_and_constants_match_wire_schema(self):
        from jsonschema import Draft202012Validator
        spec = schema("compatibility-proof.schema.json")
        paths = [("schema_version",), ("run_id",), ("backup_inventory_version",),
                 ("predecessors", 0, "migration_evidence", "target_pending_plan_count"),
                 ("predecessors", 0, "migration_measurements", 0, "wal_bytes"),
                 ("predecessors", 0, "migration_measurements", 0, "tables", 0, "reference_rows")]
        for path in paths:
            target, _, proof, _ = restore_release()
            node = proof
            for key in path[:-1]: node = node[key]
            node[path[-1]] = float(node[path[-1]])
            raw = bind_proof(target, proof)
            self.assertTrue(Draft202012Validator(spec).is_valid(proof), path)
            shape(proof, spec)
            validate_release_proof(raw, target, repository="future-agi/future-agi")
            typed = transition_measurements(proof["predecessors"][0])
            self.assertIs(type(typed.migration_evidence.target_pending_plan_count), int)
        for value in (True, 3.5, float("inf"), float("nan")):
            target, _, proof, _ = restore_release()
            proof["predecessors"][0]["migration_evidence"]["target_pending_plan_count"] = value
            self.assertFalse(Draft202012Validator(spec).is_valid(proof))
            with self.assertRaises(ValueError): shape(proof, spec)

    def test_every_proof_digest_rejects_a_final_newline_in_schema_and_python(self):
        from jsonschema import Draft202012Validator
        target, _, proof, _ = restore_release()
        paths = []
        def walk(value, path=()):
            if isinstance(value, dict):
                for key, item in value.items(): walk(item, path + (key,))
            elif isinstance(value, list):
                for key, item in enumerate(value): walk(item, path + (key,))
            elif isinstance(value, str) and (len(value) == 64 or value.startswith("sha256:") or len(value) == 40):
                paths.append(path)
        walk(proof)
        self.assertGreater(len(paths), 20)
        spec = schema("compatibility-proof.schema.json")
        validator = Draft202012Validator(spec)
        for path in paths:
            bad = copy.deepcopy(proof)
            node = bad
            for key in path[:-1]: node = node[key]
            node[path[-1]] += "\n"
            self.assertFalse(validator.is_valid(bad), path)
            with self.assertRaises(ValueError): shape(bad, spec)

    def test_notify_only_else_branch_matches_runtime_for_strategy_and_certification(self):
        from jsonschema import Draft202012Validator
        spec = schema("release-manifest.schema.json")
        validator = Draft202012Validator(spec)
        target = default_v2()
        target["compatibility"]["automatic_rollback"]["strategy"] = "restore_backup"
        self.assertFalse(validator.is_valid(target))
        with self.assertRaises(ValueError): validate_manifest(target)
        for certified in (False, True):
            target, _, _, _ = restore_release()
            compatibility = target["compatibility"]
            compatibility.update(automatic_apply=False, automatic_rollback={"strategy": "never"})
            compatibility["predecessors"][0]["restore_certified"] = certified
            compatibility["validation"] = None
            self.assertEqual(validator.is_valid(target), not certified)
            if certified:
                with self.assertRaises(ValueError): validate_manifest(target)
            else: validate_manifest(target)

    def test_all_new_closed_objects_fields_and_wire_bounds_match_schema(self):
        from jsonschema import Draft202012Validator
        spec = schema("compatibility-proof.schema.json")
        validator = Draft202012Validator(spec)
        target, _, proof, _ = restore_release()
        row = proof["predecessors"][0]
        objects = []
        def visit(value, path):
            if isinstance(value, dict):
                objects.append((path, value))
                for key, item in value.items(): visit(item, path + (key,))
            elif isinstance(value, list):
                for key, item in enumerate(value): visit(item, path + (key,))
        for field in ("migration_measurements", "backup_measurement", "restore_measurement"):
            visit(row[field], (field,))
        for path, obj in objects:
            for field in list(obj) + ["unexpected"]:
                bad = copy.deepcopy(proof)
                node = bad["predecessors"][0]
                for key in path: node = node[key]
                if field == "unexpected": node[field] = "not evidence"
                else: del node[field]
                self.assertFalse(validator.is_valid(bad), (path, field))
                with self.assertRaises(ValueError): shape(bad, spec)
        for field in ("migration_measurements", "backup_measurement", "restore_measurement"):
            bad = copy.deepcopy(proof)
            del bad["predecessors"][0][field]
            self.assertFalse(validator.is_valid(bad), field)
            with self.assertRaises(ValueError):
                validate_release_proof(bind_proof(target, bad), target, repository="future-agi/future-agi")

    def test_every_new_leaf_field_has_schema_python_rejection_and_boundary_parity(self):
        from jsonschema import Draft202012Validator
        spec = schema("compatibility-proof.schema.json")
        validator = Draft202012Validator(spec)
        cases = {
            ("migration_measurements", 0, "app_label"): ["", "0bad_app", "has.space", "x" * 129],
            ("migration_measurements", 0, "name"): ["", "has/segment", "has.space", "x" * 129],
            ("migration_measurements", 0, "engine"): ["mysql", "Postgres", None],
            ("migration_measurements", 0, "risk_class"): ["unknown", "images_only", None],
            ("migration_measurements", 0, "duration_seconds"): [0, -1, True, "5", 1e19],
            ("migration_measurements", 0, "peak_disk_amplification_factor"): [0, 0.5, 1000001, True, "2"],
            ("migration_measurements", 0, "wal_bytes"): [-1, 1.5, True, 9007199254740992],
            ("migration_measurements", 0, "temp_bytes"): [-1, 1.5, True, 9007199254740992],
            ("migration_measurements", 0, "throughput", "unit"): ["rows/s", "unknown", None],
            ("migration_measurements", 0, "throughput", "value"): [0, -1, True, "20", 1e19],
            ("migration_measurements", 0, "tables", 0, "table"): ["unqualified", "a.b.c", "public.bad;DROP", "public.has-space", "public.table\n"],
            ("migration_measurements", 0, "tables", 0, "reference_rows"): [-1, 1.5, True, 9007199254740992],
            ("migration_measurements", 0, "tables", 0, "reference_bytes"): [-1, 1.5, True, 9007199254740992],
        }
        for transfer in ("backup_measurement", "restore_measurement"):
            cases[(transfer, "throughput_bytes_per_second")] = [0, -1, True, "1000", 1e19]
            for volume in AUTOMATIC_VOLUMES:
                cases[(transfer, "reference_volume_bytes", volume)] = [-1, 1.5, True, 9007199254740992]
        for path, invalid_values in cases.items():
            for value in invalid_values:
                target, _, proof, _ = restore_release()
                node = proof["predecessors"][0]
                for key in path[:-1]: node = node[key]
                node[path[-1]] = value
                self.assertFalse(validator.is_valid(proof), (path, value))
                with self.assertRaises(ValueError): shape(proof, spec)
                with self.assertRaises(ValueError):
                    validate_release_proof(bind_proof(target, proof), target, repository="future-agi/future-agi")
        # Zero-length reference runs do not count as measured backup/restore.
        for transfer in ("backup_measurement", "restore_measurement"):
            target, _, proof, _ = restore_release()
            proof["predecessors"][0][transfer]["reference_volume_bytes"] = {name: 0 for name in AUTOMATIC_VOLUMES}
            self.assertFalse(validator.is_valid(proof))
            with self.assertRaises(ValueError):
                validate_release_proof(bind_proof(target, proof), target, repository="future-agi/future-agi")

    def test_nonfinite_measurements_including_overflow_exponents_are_never_evidence(self):
        target, _, proof, _ = restore_release()
        paths = [("duration_seconds",), ("peak_disk_amplification_factor",), ("wal_bytes",), ("temp_bytes",),
                 ("throughput", "value"), ("tables", 0, "reference_rows"), ("tables", 0, "reference_bytes")]
        for path in paths:
            for value in (float("nan"), float("inf"), -float("inf")):
                bad = copy.deepcopy(proof)
                node = bad["predecessors"][0]["migration_measurements"][0]
                for key in path[:-1]: node = node[key]
                node[path[-1]] = value
                with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                    validate_release_proof(bind_proof(target, bad), target, repository="future-agi/future-agi")
        for field in ("backup_measurement", "restore_measurement"):
            for subfield in ("throughput_bytes_per_second", "reference_volume_bytes"):
                for value in (float("nan"), float("inf")):
                    bad = copy.deepcopy(proof)
                    if subfield == "reference_volume_bytes": bad["predecessors"][0][field][subfield]["app-data"] = value
                    else: bad["predecessors"][0][field][subfield] = value
                    with self.assertRaises(ValueError):
                        validate_release_proof(bind_proof(target, bad), target, repository="future-agi/future-agi")
        with self.assertRaises(ValueError): decode_document(b'{"finite_json_syntax": 1e400}')


if __name__ == "__main__":
    unittest.main()
