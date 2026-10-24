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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.tests.test_update_manifest import valid_manifest  # noqa: E402
from deploy.update.compatibility import (  # noqa: E402
    MAX_PROOF_BYTES, PROOF_ASSET, SUITES, Attempt, CompatibilityError, Controls,
    Observations, RuntimeImage, decode_document, evaluate_automatic_apply,
    evaluate_automatic_rollback, inputs_hash, schema, shape, topology_profile,
    validate_release_proof,
)
from deploy.update.contracts import (  # noqa: E402
    AutomationPolicy, InstallationBinding, PreparedUpdate, ReasonCode, VerificationSnapshot,
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


class ApplyEligibilityTests(unittest.TestCase):
    def test_positive_all_of_synthetic_fixture(self):
        for mode, profiles in (("standalone", ()), ("standalone", ("ml", "sandbox")), ("distributed", ("full",))):
            prepared, obs, controls, proof = synthetic_context(mode, profiles)
            result = evaluate_automatic_apply(prepared, obs, controls, proof)
            self.assertTrue(result.allowed, result.reason_codes)
            self.assertFalse(result.backup_pending)
            self.assertRegex(result.fingerprint, r"^[0-9a-f]{64}$")

    def test_v1_and_default_v2_never_authorize_mutation(self):
        _, obs, controls, backup = synthetic_context()
        for target in (valid_manifest(), default_v2()):
            result = evaluate_automatic_apply(prepare(target), obs, controls, backup)
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.UNCERTIFIED_RELEASE,))

    def test_pending_only_authorizes_backup_not_mutation(self):
        prepared, obs, controls, _ = synthetic_context()
        for boundary in ("before_backup", "after_backup", "before_env_commit", "before_rollout", "unknown"):
            result = evaluate_automatic_apply(prepared, replace(obs, boundary=boundary), controls, "pending")
            self.assertEqual(result.allowed, boundary == "before_backup")
            self.assertEqual(result.backup_pending, boundary == "before_backup")

    def test_missing_backup_or_legacy_hook_success_never_qualifies(self):
        prepared, obs, controls, _ = synthetic_context()
        for backup in (None, b"", {"exit": 0}, True, "completed"):
            result = evaluate_automatic_apply(prepared, obs, controls, backup)
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.BACKUP_PROOF_INVALID,))

    def test_every_missing_host_fact_refuses(self):
        prepared, obs, controls, proof = synthetic_context()
        facts = ("online_verified", "owner_confirmed", "topology_qualified", "runtime_complete", "baseline_attested",
                 "state_unchanged", "graph_unchanged", "no_pending_transaction", "no_failed_target", "env_unchanged",
                 "no_source_build", "no_pending_migrations")
        for key in facts:
            with self.subTest(key=key):
                self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, **{key: False}), controls, proof).allowed)

    def test_every_effective_control_refuses(self):
        prepared, obs, controls, proof = synthetic_context()
        for key, value in (("enabled", False), ("paused", True), ("registered", False),
                           ("window_open", False), ("start_budget_available", False)):
            with self.subTest(key=key):
                self.assertFalse(evaluate_automatic_apply(prepared, obs, replace(controls, **{key: value}), proof).allowed)
        for mode in ("manual", "notify", "download"):
            policy = replace(controls.policy, mode=mode)
            self.assertFalse(evaluate_automatic_apply(prepared, obs, replace(controls, policy=policy), proof).allowed)

    def test_production_and_core_plan_blocks(self):
        prepared, obs, controls, proof = synthetic_context("distributed")
        production = replace(prepared, binding=replace(prepared.binding, production=True))
        self.assertEqual(evaluate_automatic_apply(production, obs, controls, proof).reason_codes,
                         (ReasonCode.INITIALIZATION_REQUIRED,))
        for change in ({"decision": "blocked"}, {"resume": True}, {"manifest_hash": "a" * 64},
                       {"staged_version": "v1.52.0"}, {"stopped_version": "v1.52.0"}, {"missing_env_keys": ["SECRET"]}):
            plan = dict(prepared.plan)
            plan.update(change)
            self.assertFalse(evaluate_automatic_apply(replace(prepared, plan=plan), obs, controls, proof).allowed)

    def test_false_verification_signer_issuer_method_and_freshness(self):
        prepared, obs, controls, proof = synthetic_context()
        for change in ({"verified": False}, {"signer": "https://github.com/evil/fork/workflow"},
                       {"issuer": "evil"}, {"method": "public-key"}, {"tool": ""},
                       {"checked_at": NOW - timedelta(seconds=301)}, {"checked_at": NOW + timedelta(seconds=1)}):
            bad = replace(prepared, verification=replace(prepared.verification, **change))
            self.assertFalse(evaluate_automatic_apply(bad, obs, controls, proof).allowed)
        self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, predecessor_verification=None), controls, proof).allowed)

    def test_same_version_different_raw_bytes_or_source_are_refused(self):
        prepared, obs, controls, proof = synthetic_context()
        variants = [obs.predecessor_raw + b"\n"]
        for key, value in (("source_sha", "a" * 40), ("published_at", "2026-10-20T00:00:00Z")):
            previous = json.loads(obs.predecessor_raw)
            previous[key] = value
            variants.append(canonical_json(previous))
        for raw in variants:
            self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, predecessor_raw=raw), controls, proof).allowed)
        # S01 validates hashes, not equality of raw bytes and parsed value. S02 does.
        changed = json.loads(prepared.raw_manifest)
        changed["release_notes"] = "https://example.com/changed"
        mismatch = replace(prepared, manifest=changed)
        self.assertFalse(evaluate_automatic_apply(mismatch, obs, controls, proof).allowed)

    def test_predecessor_digest_binding_checked_against_actual_verified_manifest(self):
        target, _, release_proof, _ = synthetic_release()
        release_proof["predecessors"][0]["predecessor_components"]["standalone"]["digest"] = "sha256:" + "f" * 64
        raw = bind_proof(target, release_proof)
        _, obs, controls, backup = synthetic_context()
        self.assertFalse(evaluate_automatic_apply(prepare(target), replace(obs, release_proof=raw), controls, backup).allowed)

    def test_live_images_ids_missing_replicas_and_dependency_changes_refuse(self):
        prepared, obs, controls, proof = synthetic_context()
        for field in ("product_images", "dependency_images"):
            original = getattr(obs, field)
            service = next(iter(original))
            for kind in ("missing", "empty", "digest", "id", "image", "extra"):
                images = dict(original)
                if kind == "missing":
                    del images[service]
                elif kind == "empty":
                    images[service] = ()
                elif kind == "extra":
                    images["custom"] = images[service]
                else:
                    key, value = {"digest": ("digest", "sha256:" + "f" * 64), "id": ("image_id", "unknown"),
                                  "image": ("image", "evil/fork:latest")}[kind]
                    images[service] = images[service] + (replace(images[service][0], **{key: value}),)
                with self.subTest(field=field, kind=kind):
                    self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, **{field: images}), controls, proof).allowed)

    def test_fingerprint_ignores_replica_order_but_binds_image_ids(self):
        prepared, obs, controls, proof = synthetic_context()
        first = obs.product_images["app"][0]
        second = replace(first, image_id="sha256:" + "f" * 64)
        forward = replace(obs, product_images={"app": (first, second)})
        reverse = replace(obs, product_images={"app": (second, first)})
        a = evaluate_automatic_apply(prepared, forward, controls, proof)
        b = evaluate_automatic_apply(prepared, reverse, controls, proof)
        self.assertTrue(a.allowed and b.allowed)
        self.assertEqual(a.fingerprint, b.fingerprint)
        changed = replace(obs, product_images={"app": (first, replace(second, image_id="sha256:" + "e" * 64))})
        c = evaluate_automatic_apply(prepared, changed, controls, proof)
        self.assertTrue(c.allowed)
        self.assertNotEqual(a.fingerprint, c.fingerprint)

    def test_all_epoch_and_configuration_bindings(self):
        prepared, obs, controls, proof = synthetic_context()
        for key in obs.state_epochs:
            epochs = dict(obs.state_epochs)
            epochs[key] += 1
            self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, state_epochs=epochs), controls, proof).allowed)
        for key, value in (("installation_fingerprint", "a" * 64), ("config_fingerprint", "changed"),
                           ("profile_fingerprint", "changed"), ("trust_fingerprint", "changed"),
                           ("persistence", {}), ("deployment_files", {}), ("platform", "linux/arm64")):
            self.assertFalse(evaluate_automatic_apply(prepared, replace(obs, **{key: value}), controls, proof).allowed)

    def test_required_env_present_nonempty_scoped_and_redacted(self):
        target, _, release, _ = synthetic_release()
        target["compatibility"]["required_env"] = [
            {"name": "SYNTHETIC_SECRET", "rule": "nonempty", "components": ["standalone"]},
            {"name": "UNDEPLOYED_SECRET", "rule": "nonempty", "components": ["backend"]}]
        raw = bind_proof(target, release)
        prepared = prepare(target)
        _, obs, controls, backup = synthetic_context()
        obs = replace(obs, release_proof=raw)
        backup_value = json.loads(backup)
        backup_value["target_manifest_hash"] = manifest_hash(target)
        backup = canonical_json(backup_value)
        for env in ({}, {"SYNTHETIC_SECRET": ""}, {"SYNTHETIC_SECRET": "  "}):
            result = evaluate_automatic_apply(prepared, replace(obs, env=env), controls, backup)
            self.assertEqual(result.reason_codes, (ReasonCode.REQUIRED_ENV_MISSING,))
            self.assertNotIn("SECRET", json.dumps(result.public_summary()))
        self.assertTrue(evaluate_automatic_apply(prepared, replace(obs, env={"SYNTHETIC_SECRET": "private-value"}), controls, backup).allowed)

    def test_version_policy_and_channel_never_inferred_from_certificate(self):
        prepared, obs, controls, proof = synthetic_context()
        policy = replace(controls.policy, version_policy="patch")
        self.assertEqual(evaluate_automatic_apply(prepared, obs, replace(controls, policy=policy), proof).reason_codes,
                         (ReasonCode.VERSION_POLICY_BLOCKED,))
        policy = replace(controls.policy, channel="beta")
        self.assertEqual(evaluate_automatic_apply(prepared, obs, replace(controls, policy=policy), proof).reason_codes,
                         (ReasonCode.CHANNEL_MISMATCH,))

    def test_backup_stale_wrong_attempt_and_incomplete_are_not_eligible(self):
        prepared, obs, controls, backup = synthetic_context()
        for key, value in (("attempt_id", "b" * 32), ("covered_domains", ["postgres_app"]),
                           ("verification", "failed"), ("completed_at", stamp(NOW + timedelta(seconds=1)))):
            proof = json.loads(backup)
            proof[key] = value
            self.assertFalse(evaluate_automatic_apply(prepared, obs, controls, canonical_json(proof)).allowed)

    def test_untrusted_errors_do_not_escape_or_echo_private_data(self):
        prepared, obs, controls, backup = synthetic_context()
        bad = replace(obs, release_proof=b'{"secret":"/private/path password=secret"}')
        result = evaluate_automatic_apply(prepared, bad, controls, backup)
        self.assertEqual(result.public_summary(), {"eligible": False, "reason_codes": ["uncertified_release"]})


class RollbackEligibilityTests(unittest.TestCase):
    def context(self):
        prepared, obs, controls, backup = synthetic_context()
        prior_images = obs.product_images
        layout = topology_profile(prepared.binding.topology, prepared.binding.profiles)
        obs = replace(obs, boundary="before_fallback",
                      product_images=runtime_images(json.loads(prepared.raw_manifest)["components"], layout))
        attempt = Attempt(prepared, obs.attempt_id, RESERVED, backup, NOW - timedelta(seconds=30),
                          rollback_enabled_before_start=True, automatic=True, live=True, latest_attempt_id=obs.attempt_id,
                          failure_kind="readiness_failed", child_state_known=True, prior_images=prior_images,
                          prior_images_available=True, receipt_matches=True)
        return attempt, obs, controls

    def test_one_exact_qualified_images_only_fallback(self):
        attempt, obs, controls = self.context()
        result = evaluate_automatic_rollback(attempt, obs, controls)
        self.assertTrue(result.allowed, result.reason_codes)
        self.assertIsNotNone(result.fingerprint)

    def test_default_off_and_every_attempt_predicate_refuses(self):
        attempt, obs, controls = self.context()
        changes = ({"rollback_enabled_before_start": False}, {"automatic": False}, {"live": False},
                   {"latest_attempt_id": "b" * 32}, {"child_state_known": False}, {"fallback_count": 1},
                   {"fallback_count": False}, {"prior_images_available": False}, {"receipt_matches": False})
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(evaluate_automatic_rollback(replace(attempt, **change), obs, controls).allowed)
        self.assertFalse(evaluate_automatic_rollback(attempt, obs, replace(controls, policy=replace(controls.policy, automatic_rollback=False))).allowed)

    def test_interruption_timeout_download_and_pre_rollout_never_fallback(self):
        attempt, obs, controls = self.context()
        for kind in ("interrupted", "timeout", "download_failed", "verification_failed", "pre_rollout_failed", "unknown", "succeeded"):
            with self.subTest(kind=kind):
                self.assertFalse(evaluate_automatic_rollback(replace(attempt, failure_kind=kind), obs, controls).allowed)
        self.assertTrue(evaluate_automatic_rollback(replace(attempt, failure_kind="rollout_failed"), obs, controls).allowed)

    def test_post_rollout_pause_or_window_closure_does_not_cancel_safety_action(self):
        attempt, obs, controls = self.context()
        self.assertTrue(evaluate_automatic_rollback(attempt, obs, replace(controls, enabled=False, paused=True, window_open=False)).allowed)

    def test_original_proof_is_checked_at_first_mutation_not_refreshed(self):
        attempt, obs, controls = self.context()
        # Same live attempt can be slow; pre-migration proof remains the recovery point.
        self.assertTrue(evaluate_automatic_rollback(attempt, replace(obs, now=NOW + timedelta(hours=2)), controls).allowed)
        proof = json.loads(attempt.backup_raw)
        proof["started_at"] = stamp(NOW)
        proof["completed_at"] = stamp(NOW)
        self.assertFalse(evaluate_automatic_rollback(replace(attempt, backup_raw=canonical_json(proof)), obs, controls).allowed)
        self.assertFalse(evaluate_automatic_rollback(replace(attempt, first_mutation_at=NOW + timedelta(hours=2)),
                                                    replace(obs, now=NOW + timedelta(hours=2)), controls).allowed)

    def test_prior_images_and_current_state_are_not_assumed_healthy(self):
        attempt, obs, controls = self.context()
        self.assertFalse(evaluate_automatic_rollback(replace(attempt, prior_images={}), obs, controls).allowed)
        self.assertFalse(evaluate_automatic_rollback(attempt, replace(obs, state_unchanged=False), controls).allowed)
        self.assertFalse(evaluate_automatic_rollback(attempt, replace(obs, dependency_images={}), controls).allowed)
        self.assertFalse(evaluate_automatic_rollback(attempt, replace(obs, no_pending_transaction=False), controls).allowed)


if __name__ == "__main__":
    unittest.main()
