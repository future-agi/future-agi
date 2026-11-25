"""S11 daemon-free release contracts. Every evidence/signature fixture is SYNTHETIC.

These tests execute workflow shell, but never call real gh/cosign/docker/ORAS.
They do not qualify a product image, issue an identity or authorize a release.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.tests import test_update_release_static as release  # noqa: E402
from deploy.tests.test_update_compatibility import default_v2, synthetic_release  # noqa: E402
from deploy.update.compatibility import (  # noqa: E402
    PROOF_ASSET, LEGACY_SUITES as SUITES, CompatibilityError, inputs_hash, topology_profile, validate_release_proof,
)
from deploy.update.manifest import canonical_json, is_version, manifest_hash, parse_version  # noqa: E402
from scripts import build_update_compatibility_proof as producer  # noqa: E402

SMOKE = release.WORKFLOWS / "release-signing-smoke.yml"
PROOF = release.WORKFLOWS / "update-compatibility-proof.yml"
needs_jsonschema = unittest.skipUnless(importlib.util.find_spec("jsonschema"), "optional jsonschema is not installed")
FAKE_METADATA = '''
import json, os, sys
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(json.dumps(['gh'] + sys.argv[1:]) + '\\n')
assert sys.argv[1:3] == ['release', 'view']
print(os.environ['METADATA'])
sys.exit(int(os.environ.get('METADATA_EXIT', '0')))
'''


class BetaWorkflowTests(release._StepHarness):
    def setUp(self):
        super().setUp()
        release.write_fake(self.bin, "gh", FAKE_METADATA)
        release.write_fake(self.bin, "curl", 'import os; print(os.environ.get("CURL_STATUS", "404"))')
        release.write_fake(self.bin, "git", release.FAKE_GIT_TAGS)
        self.output = self.tmp / "output"

    def guard(self, path, version, *, ref=None, repo=release.CANONICAL_REPOSITORY,
              event="push", components="", metadata=None, status="404", smoke="true"):
        doc = release.load(path)
        key = "guard" if path == release.RELEASE_IMAGES else "package" if path == release.HELM_RELEASE else "generate"
        job = doc["jobs"][key]
        step = job["steps"][0]
        metadata = metadata if metadata is not None else {"tagName": version, "isDraft": False, "isPrerelease": "-" in version}
        env = self.base_env(VERSION=version, REQUESTED_VERSION=version, REQUESTED_COMPONENTS=components,
                            GITHUB_REF=ref if ref is not None else "refs/tags/" + version,
                            GITHUB_REF_NAME=version, GITHUB_REPOSITORY=repo, GITHUB_EVENT_NAME=event,
                            GITHUB_OUTPUT=str(self.output), GITHUB_SERVER_URL="https://github.com", SMOKE=smoke,
                            METADATA=json.dumps(metadata), CURL_STATUS=status, FAKE_TAGS="v1.60.0", GH_TOKEN="fake")
        self.output.write_text("")
        return release.run_step(step, env, self.tmp, self.scratch)

    def test_all_three_guards_share_the_host_version_grammar(self):
        versions = ["v1.60.0", "v1.60.0-beta.0", "v1.60.0-beta.12", "v1.60.0-rc.2",
                    "v01.60.0", "v1.60.0-rc.01", "v1.60.0-alpha.1", "v1.60.0+build", "v1.60.0\n",
                    "v1.60.0-rc.1000000000000000000", "v1.60.0-beta", "v1.60.0-rc.-1"]
        for path in (release.UPDATE_WORKFLOW, release.HELM_RELEASE, release.RELEASE_IMAGES):
            for version in versions:
                with self.subTest(path=path.name, version=version):
                    result = self.guard(path, version)
                    self.assertEqual(result.returncode == 0, is_version(version), result.stdout + result.stderr)

    def test_repo_and_ref_rejected_before_any_tool(self):
        for path in (release.UPDATE_WORKFLOW, release.HELM_RELEASE, release.RELEASE_IMAGES):
            for kwargs in ({"repo": "someone/fork"}, {"ref": "refs/heads/dev"}, {"ref": "refs/tags/v1.60.0"}):
                with self.subTest(path=path, kwargs=kwargs):
                    self.log.unlink(missing_ok=True)
                    result = self.guard(path, "v1.60.0-beta.1", **kwargs)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(release.calls(self.log), [])

    def test_beta_selects_complete_oss_product_and_no_stable_dispatch(self):
        for suffix in ("beta.1", "rc.2"):
            result = self.guard(release.RELEASE_IMAGES, "v1.60.0-" + suffix)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = dict(line.split("=", 1) for line in self.output.read_text().splitlines())
            self.assertEqual(values["channel"], "beta")
            components = set(json.loads(values["components"]))
            self.assertEqual(components, {"frontend", "agentcc-gateway", "serving", "fi-collector", "serving-gpu",
                                         "code-executor", "backend", "backend-slim", "standalone", "simulation-runner",
                                         "helm-chart", "update-manifest"})
        for component in ("ee", "docs", "bump"):
            result = self.guard(release.RELEASE_IMAGES, "v1.60.0-beta.1", event="workflow_dispatch", components=component)
            self.assertNotEqual(result.returncode, 0)

    def test_beta_push_and_dispatch_refuse_existing_or_unknown_images(self):
        for event in ("push", "workflow_dispatch"):
            for status in ("200", "401", "500", "000"):
                with self.subTest(event=event, status=status):
                    result = self.guard(release.RELEASE_IMAGES, "v1.60.0-rc.1", event=event,
                                        components="backend", status=status)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("components=", self.output.read_text())
        result = self.guard(release.RELEASE_IMAGES, "v1.60.0-beta.1", event="workflow_dispatch", components="update-manifest")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_beta_cannot_skip_helm_smoke(self):
        self.assertNotEqual(self.guard(release.HELM_RELEASE, "v1.60.0-beta.1", smoke="false").returncode, 0)
        self.assertEqual(self.guard(release.HELM_RELEASE, "v1.60.0", smoke="false").returncode, 0)
        doc = release.load(release.HELM_RELEASE)
        self.assertIn("!contains(inputs.version, '-')", doc["jobs"]["publish"]["if"])
        metadata = release.one_step(doc["jobs"]["publish"], lambda step: step.get("id") == "artifacthub")
        self.assertEqual(metadata["if"], "${{ !contains(inputs.version, '-') }}")

    def test_release_metadata_requires_exact_tag_draft_and_channel(self):
        for path in (release.UPDATE_WORKFLOW, release.HELM_RELEASE):
            doc = release.load(path)
            key = "generate" if path == release.UPDATE_WORKFLOW else "package"
            step = release.one_step(doc["jobs"][key], lambda s: s.get("name") == "Release metadata")
            for version in ("v1.60.0", "v1.60.0-beta.1", "v1.60.0-rc.1"):
                base = {"tagName": version, "isDraft": False, "isPrerelease": "-" in version}
                for change in ({}, {"tagName": "v1.59.0"}, {"isDraft": True}, {"isPrerelease": not base["isPrerelease"]}):
                    with self.subTest(path=path, version=version, change=change):
                        env = self.base_env(VERSION=version, GITHUB_REPOSITORY=release.CANONICAL_REPOSITORY,
                                            METADATA=json.dumps(dict(base, **change)))
                        result = release.run_step(step, env, self.tmp, self.scratch)
                        self.assertEqual(result.returncode == 0, not change, result.stderr)

    def test_beta_generator_sets_beta_and_remains_uncertified(self):
        step = release.one_step(self.doc["jobs"]["generate"], release.runs_containing("scripts/generate_update_manifest.py"))
        (self.bin / "git").unlink()  # generation must inspect the real requested checkout
        release.write_fake(self.bin, "python3", "import os, sys; os.execv(sys.executable, [sys.executable] + sys.argv[1:])")
        # write_fake pins the current interpreter; real generator runs, no fake data processing.
        for suffix in ("beta.1", "rc.1"):
            version = release.next_version_after_floor() + "-" + suffix
            runner = self.tmp / suffix
            runner.mkdir()
            (runner / "components.txt").write_text("".join(
                f"{name}=docker.io/{repo}:{version}{release.COMPONENT_TAG_SUFFIX.get(name, '')}@{release.fake_digest(name)}\n"
                for name, repo in release.COMPONENT_REPOSITORIES.items()))
            env = self.base_env(VERSION=version, RUNNER_TEMP=str(runner), GITHUB_REPOSITORY=release.CANONICAL_REPOSITORY,
                                CHART_DIGEST=release.fake_digest("chart"), OIDC_ISSUER=release.DEFAULT_TRUSTED_ISSUER)
            result = release.run_step(step, env, ROOT, self.scratch)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((runner / "out" / release.MANIFEST_ASSET).read_bytes())
            self.assertEqual(manifest["channel"], "beta")
            self.assertEqual(manifest["version"], version)
            self.assertFalse(manifest["compatibility"]["automatic_apply"])
            self.assertIsNone(manifest["compatibility"]["validation"])
            self.assertEqual(release.validate_manifest(manifest), manifest)
            self.assertEqual(release.trust.workflow_claims(release.DEFAULT_TRUSTED_IDENTITY, version),
                             (release.CANONICAL_REPOSITORY, "refs/tags/" + version))

    def test_real_builder_shell_cannot_move_any_stable_alias_for_beta(self):
        doc = release.load(release.BUILD_MULTIARCH)
        step = release.one_step(doc["jobs"]["publish"], release.runs_containing("docker buildx imagetools create"))
        release.write_fake(self.bin, "docker", release.FAKE_IMAGE_PUBLISHER)
        digests = self.tmp / "digests"
        digests.mkdir()
        (digests / ("a" * 64)).touch()
        for version in ("v1.60.0-beta.1", "v1.60.0-rc.1"):
            for suffix in ("", "-slim", "-gpu"):
                self.log.unlink(missing_ok=True)
                env = self.base_env(VERSION=version, IMAGE="docker.io/futureagi/synthetic", MINOR="v1.60",
                                    SUFFIX=suffix, PUSH_LATEST="true",  # even if a caller requests it
                                    FAKE_CANDIDATE='{"schemaVersion":2,"manifests":[]}')
                result = release.run_step(step, env, digests, self.scratch)
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = release.calls(self.log)
                self.assertEqual([command[3] for command in commands], ['create', 'inspect', 'create'])
                self.assertIn('--dry-run', commands[0])
                self.assertNotIn('-t', commands[0])
                self.assertEqual(commands[1][4], "docker.io/futureagi/synthetic:" + version + suffix)
                command = commands[2]
                tags = [command[i + 1] for i, arg in enumerate(command) if arg == "-t"]
                self.assertEqual(tags, ["docker.io/futureagi/synthetic:" + version + suffix])
        for job in release.load(release.RELEASE_IMAGES)["jobs"].values():
            if job.get("uses") == "./.github/workflows/build-image-multiarch.yml":
                self.assertEqual(job["with"]["push-latest"], "${{ needs.guard.outputs.channel == 'stable' }}")
                self.assertIn("github.run_attempt == 1", job["with"]["enabled"])

    def test_stable_and_beta_manifest_channels_cannot_be_relabelled(self):
        for version, channel in (("v1.60.0-beta.1", "stable"), ("v1.60.0-rc.1", "stable")):
            target = default_v2(version)
            target["channel"] = channel
            with self.subTest(version=version), self.assertRaises(release.ManifestError):
                release.validate_manifest(target)
        self.assertLess(parse_version("v1.60.0-beta.10"), parse_version("v1.60.0-rc.0"))
        self.assertLess(parse_version("v1.60.0-rc.9"), parse_version("v1.60.0"))


class ImmutablePrereleasePublisherTests(release._StepHarness):
    """Run the real late publisher, with no prepare/guard job to protect a rerun."""

    def setUp(self):
        super().setUp()
        self.step = release.one_step(release.load(release.BUILD_MULTIARCH)['jobs']['publish'],
                                     release.runs_containing('Immutable prerelease'))
        release.write_fake(self.bin, 'docker', release.FAKE_IMAGE_PUBLISHER)
        # The hashing and JSON parsing are real, not registry-client stubs.
        release.write_fake(self.bin, 'python3',
                           'import os, sys; os.execv(sys.executable, [sys.executable] + sys.argv[1:])')
        self.digests = self.tmp / 'digests'
        self.digests.mkdir()
        for digest in ('a' * 64, 'b' * 64):
            (self.digests / digest).touch()
        self.manifest = json.dumps({
            'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
            'manifests': [{'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                           'digest': 'sha256:' + letter * 64, 'size': 123,
                           'platform': {'os': 'linux', 'architecture': arch}}
                          for letter, arch in (('a', 'amd64'), ('b', 'arm64'))],
        }, indent=2)
        self.candidate = 'sha256:' + hashlib.sha256(self.manifest.encode()).hexdigest()

    def publish(self, version='v1.60.0-beta.1', suffix='', **extra):
        self.log.unlink(missing_ok=True)
        env = self.base_env(IMAGE='docker.io/futureagi/synthetic', VERSION=version, MINOR='v1.60',
                            SUFFIX=suffix, PUSH_LATEST='true', FAKE_CANDIDATE=self.manifest)
        env.update(extra)
        return release.run_step(self.step, env, self.digests, self.scratch)

    def assert_no_write(self):
        commands = release.calls(self.log)
        self.assertTrue(commands)
        self.assertEqual(commands[0][3:5], ['create', '--dry-run'])
        self.assertFalse(any(command[3] == 'create' and '--dry-run' not in command for command in commands))
        self.assertFalse(any('-t' in command for command in commands))
        return commands

    def test_same_digest_retry_succeeds_without_rewriting_any_tag(self):
        for version in ('v1.60.0-beta.0', 'v1.60.0-rc.12'):
            for suffix in ('', '-slim', '-gpu'):
                with self.subTest(version=version, suffix=suffix):
                    result = self.publish(version, suffix, FAKE_EXISTING=self.candidate)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('no tag is rewritten', result.stdout)
                    self.assertEqual(len(self.assert_no_write()), 2)
                    self.assertIn(self.candidate, result.stdout)

    def test_different_digest_retry_refuses_and_names_tag_and_both_digests(self):
        existing = 'sha256:' + 'f' * 64
        for version in ('v1.60.0-beta.1', 'v1.60.0-rc.1'):
            for suffix in ('', '-slim', '-gpu'):
                with self.subTest(version=version, suffix=suffix):
                    result = self.publish(version, suffix, FAKE_EXISTING=existing)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('::error::Immutable prerelease docker.io/futureagi/synthetic:' + version + suffix,
                                  result.stdout)
                    self.assertIn(existing, result.stdout)
                    self.assertIn(self.candidate, result.stdout)
                    self.assertIn('never replace', result.stdout)
                    self.assertEqual(len(self.assert_no_write()), 2)

    def test_only_exact_missing_tag_allows_creation_after_the_late_check(self):
        for version in ('v1.60.0-beta.1', 'v1.60.0-rc.1'):
            result = self.publish(version)
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = release.calls(self.log)
            self.assertEqual([command[3] for command in commands], ['create', 'inspect', 'create'])
            self.assertEqual(commands[0][4], '--dry-run')
            self.assertEqual(commands[0][5:], commands[2][6:])  # exact same ordered digest sources
            self.assertEqual(commands[2][4:6], ['-t', 'docker.io/futureagi/synthetic:' + version])
            self.assertEqual(len(commands[2]), 8)

    def test_unknown_registry_errors_and_malformed_digests_fail_closed(self):
        ref = 'docker.io/futureagi/synthetic:v1.60.0-beta.1'
        for error in ('401 Unauthorized', '429 Too Many Requests', '500 Internal Server Error',
                      'timeout', '', 'not found', 'ERROR: other/repository: not found',
                      'ERROR: ' + ref + ': not found\ntransport failure'):
            with self.subTest(error=error):
                result = self.publish(FAKE_EXISTING='error', FAKE_INSPECT_ERROR=error)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Cannot establish', result.stdout)
                self.assert_no_write()
        for digest in ('', 'not-a-digest', 'sha256:' + 'A' * 64, 'sha256:' + 'a' * 63):
            with self.subTest(digest=digest):
                result = self.publish(FAKE_EXISTING=digest)
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_write()

    def test_failed_candidate_resolution_never_inspects_or_writes(self):
        result = self.publish(FAKE_DRY_RUN_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.assert_no_write()), 1)

    def test_candidate_digest_preserves_manifest_whitespace(self):
        # Buildx adds one newline even when copied manifest bytes end in whitespace.
        # Removing all trailing newlines or reserializing JSON would change its digest.
        raw = self.manifest + '\n\n'
        digest = 'sha256:' + hashlib.sha256(raw.encode()).hexdigest()
        result = self.publish(FAKE_CANDIDATE=raw, FAKE_EXISTING=digest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(digest, result.stdout)
        self.assert_no_write()


class ProofProducerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.fixture()

    def fixture(self, mode="standalone", profiles=(), platform="linux/amd64"):
        target, previous, proof, _ = synthetic_release(mode, profiles, platform)
        self.policy = {"schema_version": 1, "migration": copy.deepcopy(target["migration"]),
                       "compatibility": copy.deepcopy(target["compatibility"])}
        self.policy["compatibility"]["validation"] = None
        self.target = default_v2()
        # Historical contract-v1 packaging fixture only. Never derive/clear the
        # frozen migration metadata of a restore-backed contract-v2 candidate.
        self.target['migration'] = copy.deepcopy(self.policy['migration'])
        self.evidence = self.tmp / (mode + platform.replace("/", "-") + "-".join(profiles))
        self.evidence.mkdir(exist_ok=True)
        row = proof["predecessors"][0]
        (self.evidence / (row["predecessor"]["raw_manifest_sha256"] + ".json")).write_bytes(previous)
        binding = {k: copy.deepcopy(row[k]) for k in ("predecessor", "dependencies", "persistence")}
        reports = {}
        layout = topology_profile(mode, profiles)
        props = {"inputs_hash": inputs_hash(target), "binding_hash": manifest_hash(binding),
                 "repository": producer.REPOSITORY, "run_id": "42"}
        self.layout = layout
        for suite in SUITES:
            cases = producer.required_cases(suite, layout)
            xml = ET.Element("testsuite", name=suite, tests=str(len(cases)), failures="0", errors="0", skipped="0")
            properties = ET.SubElement(xml, "properties")
            for key, value in props.items():
                ET.SubElement(properties, "property", name=key, value=value)
            for case in sorted(cases):
                ET.SubElement(xml, "testcase", classname=suite, name=case, assertions="2", time="0.01")
            raw = ET.tostring(xml)
            path = suite + ".xml"
            (self.evidence / path).write_bytes(raw)
            reports[suite] = {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
        self.envelope = {"schema_version": 1, "inputs_hash": inputs_hash(target), "run_id": 42,
                         "repository": producer.REPOSITORY, "rows": [dict(binding, reports=reports)]}
        self.save()

    def save(self):
        (self.evidence / "evidence.json").write_bytes(canonical_json(self.envelope))

    def build(self, **kwargs):
        return producer.build(self.target, self.policy, self.evidence, **dict({"run_id": 42, "lane": "synthetic"}, **kwargs))

    def test_complete_synthetic_evidence_round_trips_exactly(self):
        for mode, profiles, platform in (("standalone", (), "linux/amd64"), ("standalone", ("ml", "sandbox"), "linux/arm64"),
                                          ("distributed", (), "linux/arm64"), ("distributed", ("full",), "linux/amd64")):
            with self.subTest(mode=mode, profiles=profiles, platform=platform):
                self.fixture(mode, profiles, platform)
                raw, manifest = self.build()
                target = json.loads(manifest)
                validated = validate_release_proof(raw, target, repository=producer.REPOSITORY)
                self.assertEqual(validated["inputs_hash"], inputs_hash(target))
                self.assertEqual(raw, self.build()[0])
                self.assertEqual(validated["predecessors"][0]["persistence"], dict(self.layout.persistence))
                self.assertNotIn(b".xml", raw)

    @needs_jsonschema
    def test_proof_also_matches_optional_jsonschema(self):
        import jsonschema
        raw, _ = self.build()
        schema = json.loads((release.UPDATE_DIR / "compatibility-proof.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(json.loads(raw))

    def test_legacy_fixture_packaging_cannot_issue_new_product_qualification(self):
        with self.assertRaises(CompatibilityError):
            self.build(lane='product')

    def test_default_policy_cannot_be_certified_even_with_evidence(self):
        self.policy = json.loads((release.UPDATE_DIR / "release-policy.json").read_text())
        with self.assertRaises(CompatibilityError):
            self.build()

    def test_nothing_and_passing_claims_are_not_evidence(self):
        for replacement in ({}, {suite: "passed" for suite in SUITES}, {"waiver": True}, {"run_id": 42}):
            (self.evidence / "evidence.json").write_bytes(canonical_json(replacement))
            with self.subTest(value=replacement), self.assertRaises(CompatibilityError):
                self.build()
        (self.evidence / "evidence.json").unlink()
        with self.assertRaises(OSError):
            self.build()

    def test_each_binding_change_and_extra_waiver_is_rejected(self):
        original = copy.deepcopy(self.envelope)
        for key, value in (("inputs_hash", "f" * 64), ("repository", "some/fork"), ("run_id", 43),
                           ("run_id", True), ("rows", []), ("waiver", "approved")):
            self.envelope = dict(original, **{key: value})
            self.save()
            with self.subTest(key=key, value=value), self.assertRaises(CompatibilityError):
                self.build()

    def test_target_digest_or_deployment_drift_rejected(self):
        original = copy.deepcopy(self.target)
        for path in (("components", "backend", "digest"), ("deployment", "files", "docker-compose.yml")):
            self.target = copy.deepcopy(original)
            parent = self.target
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = "sha256:" + "e" * 64 if path[-1] == "digest" else "e" * 64
            with self.subTest(path=path), self.assertRaises(release.ManifestError):
                self.build()

    def test_predecessor_bytes_cannot_be_substituted(self):
        previous = self.envelope["rows"][0]["predecessor"]
        path = self.evidence / (previous["raw_manifest_sha256"] + ".json")
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(CompatibilityError):
            self.build()

    def test_every_report_is_required_and_its_bytes_pinned(self):
        original = copy.deepcopy(self.envelope)
        for suite in SUITES:
            for mode in ("missing", "tampered", "verdict_only"):
                self.envelope = copy.deepcopy(original)
                reports = self.envelope["rows"][0]["reports"]
                if mode == "missing":
                    del reports[suite]
                elif mode == "tampered":
                    reports[suite]["sha256"] = "0" * 64
                else:
                    reports[suite] = "passed"
                self.save()
                with self.subTest(suite=suite, mode=mode), self.assertRaises(CompatibilityError):
                    self.build()

    def test_junit_fail_skip_unknown_zero_assertions_or_incomplete_domains_rejected(self):
        suite = "bidirectional_read_write"
        path = self.evidence / (suite + ".xml")
        original = path.read_bytes()
        for mutation in ("failure", "error", "skipped", "missing", "zero", "wrong_binding", "duplicate", "unknown", "dtd"):
            root = ET.fromstring(original)
            case = root.findall("testcase")[0]
            if mutation in ("failure", "error", "skipped"):
                ET.SubElement(case, mutation)
            elif mutation == "missing":
                root.remove(case)
            elif mutation == "zero":
                case.set("assertions", "0")
            elif mutation == "wrong_binding":
                root.find("properties")[0].set("value", "f" * 64)
            elif mutation == "duplicate":
                root.findall("testcase")[1].set("name", case.get("name"))
            elif mutation == "unknown":
                case.set("name", "unrelated_test")
            raw = ET.tostring(root)
            if mutation == "dtd":
                raw = b'<!DOCTYPE testsuite [<!ENTITY x "ignored">]>' + raw
            path.write_bytes(raw)
            self.envelope["rows"][0]["reports"][suite]["sha256"] = hashlib.sha256(raw).hexdigest()
            self.save()
            with self.subTest(mutation=mutation), self.assertRaises(CompatibilityError):
                self.build()

    def test_paths_symlinks_duplicate_keys_and_large_inputs_refused(self):
        original = copy.deepcopy(self.envelope)
        for name in ("../escape.xml", "/tmp/escape.xml", "nested/file.xml"):
            self.envelope = copy.deepcopy(original)
            self.envelope["rows"][0]["reports"][SUITES[0]]["path"] = name
            self.save()
            with self.assertRaises(CompatibilityError):
                self.build()
        self.envelope = original
        self.save()
        path = self.evidence / (SUITES[0] + ".xml")
        path.unlink()
        path.symlink_to(self.evidence / (SUITES[1] + ".xml"))
        with self.assertRaises(OSError):
            self.build()
        (self.evidence / "evidence.json").write_bytes(b'{"run_id":42,"run_id":42}')
        with self.assertRaises(CompatibilityError):
            self.build()
        (self.evidence / "evidence.json").write_bytes(b" " * (producer.MAX_PROOF_BYTES + 1))
        with self.assertRaises(CompatibilityError):
            self.build()

    def test_actual_publish_validation_rejects_tampered_missing_and_wrong_run_proof(self):
        raw, manifest = self.build()
        directory = self.tmp / "new"
        directory.mkdir()
        (directory / release.MANIFEST_ASSET).write_bytes(manifest)
        proof = directory / PROOF_ASSET
        proof.write_bytes(raw)
        frozen = self.tmp / 'frozen'
        frozen.mkdir()
        (frozen / release.MANIFEST_ASSET).write_bytes(canonical_json(self.target))
        binary = self.tmp / "bin"
        binary.mkdir()
        release.write_fake(binary, "python3", "import os, sys; os.execv(sys.executable, [sys.executable] + sys.argv[1:])")
        step = release.one_step(release.load(release.UPDATE_WORKFLOW)["jobs"]["publish"],
                                lambda s: s.get("name") == "Validate proof before signing")
        env = {"PATH": str(binary) + ":" + os.environ.get("PATH", ""), "RUNNER_TEMP": str(self.tmp),
               "MANIFEST": release.MANIFEST_ASSET, "GITHUB_RUN_ID": "42", "PYTHONDONTWRITEBYTECODE": "1",
               "GITHUB_REPOSITORY": producer.REPOSITORY, "GITHUB_SHA": self.target['source_sha'],
               "VERSION": self.target['version'], "GITHUB_REF": 'refs/tags/'+self.target['version']}
        result = release.run_step(step, env, ROOT, self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        for changed, run in ((raw + b" ", "42"), (raw, "43"), (None, "42")):
            if changed is None:
                proof.unlink()
            else:
                proof.write_bytes(changed)
            result = release.run_step(step, dict(env, GITHUB_RUN_ID=run), ROOT, self.tmp)
            self.assertNotEqual(result.returncode, 0)
        proof.write_bytes(raw)
        for changed_env in (dict(env,GITHUB_SHA='f'*40),dict(env,GITHUB_REF='refs/heads/dev')):
            result = release.run_step(step, changed_env, ROOT, self.tmp)
            self.assertNotEqual(result.returncode, 0)
        changed_candidate = copy.deepcopy(self.target)
        changed_candidate['published_at'] = '2026-10-21T00:00:00Z'
        (frozen / release.MANIFEST_ASSET).write_bytes(canonical_json(changed_candidate))
        result = release.run_step(step, env, ROOT, self.tmp)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_refuses_missing_evidence_without_creating_outputs(self):
        target, policy = self.tmp / "target.json", self.tmp / "policy.json"
        target.write_bytes(canonical_json(self.target))
        policy.write_bytes(canonical_json(self.policy))
        outputs = (self.tmp / "proof.json", self.tmp / "candidate.json")
        result = subprocess.run([sys.executable, str(Path(producer.__file__)), "--target", str(target),
                                 "--policy", str(policy), "--evidence", str(self.tmp / "absent"),
                                 "--run-id", "42", "--output", str(outputs[0]), "--manifest-output", str(outputs[1])],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "compatibility evidence rejected; operator action required\n")
        self.assertFalse(any(path.exists() for path in outputs))

    def test_local_outputs_never_replace_different_bytes_and_retry_matches(self):
        raw, manifest = self.build()
        outputs = ((self.tmp / "proof.json", raw), (self.tmp / "candidate.json", manifest))
        producer.write_pair(outputs)
        producer.write_pair(outputs)
        self.assertEqual(outputs[0][0].read_bytes(), raw)
        outputs[1][0].write_bytes(b"do not replace")
        with self.assertRaises(CompatibilityError):
            producer.write_pair(outputs)
        self.assertEqual(outputs[1][0].read_bytes(), b"do not replace")


class SmokeExecutionTests(release._StepHarness):
    def setUp(self):
        super().setUp()
        self.doc = release.load(SMOKE)
        self.job = self.doc["jobs"]["keyless"]
        self.env = self.base_env(**self.job["env"], RUNNER_TEMP=str(self.tmp),
                                 GITHUB_SHA="a" * 40, GITHUB_RUN_ID="42", GITHUB_RUN_ATTEMPT="1",
                                 GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/dev",
                                 GITHUB_REPOSITORY=release.CANONICAL_REPOSITORY, REF_PROTECTED="true")
        release.write_fake(self.bin, "python3", "import os, sys; os.execv(sys.executable, [sys.executable] + sys.argv[1:])")
        release.write_fake(self.bin, "cosign", '''
import hashlib, json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(['cosign'] + args) + '\\n')
if args[0] == 'version':
    print(json.dumps({'gitVersion': 'v3.1.3'})); sys.exit(0)
flag = lambda name: args[args.index(name) + 1]
raw = Path(args[-1]).read_bytes()
expected = {'sha256': hashlib.sha256(raw).hexdigest(), 'identity': os.environ['CERT_IDENTITY'],
            'issuer': os.environ['OIDC_ISSUER'], 'repo': os.environ['CERT_REPOSITORY'], 'ref': os.environ['CERT_REF']}
if args[0] == 'sign-blob':
    assert '--yes' in args
    Path(flag('--bundle')).write_text(json.dumps(expected)); sys.exit(0)
assert args[0] == 'verify-blob'
actual = json.loads(Path(flag('--bundle')).read_text())
for key, name in [('identity', '--certificate-identity'), ('issuer', '--certificate-oidc-issuer'),
                  ('repo', '--certificate-github-workflow-repository'), ('ref', '--certificate-github-workflow-ref')]:
    expected[key] = flag(name)
sys.exit(0 if expected == actual else 1)
''')

    def test_real_smoke_shell_signs_only_smoke_and_runs_all_negative_controls(self):
        for name in ("Reviewed ref only", "Non-production subject", "Keyless sign and exact-claim controls",
                     "Default production host trust rejects smoke"):
            step = release.one_step(self.job, lambda s: s.get("name") == name)
            result = release.run_step(step, self.env, ROOT, self.scratch)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = release.calls(self.log)
        self.assertEqual(sum(c[1] == "sign-blob" for c in calls), 1)
        self.assertEqual(sum(c[1] == "verify-blob" for c in calls), 7)
        subject = json.loads((self.tmp / "CI-SMOKE-ONLY.json").read_text())
        self.assertEqual(subject["purpose"], "CI-SMOKE-ONLY-NOT-A-RELEASE")
        self.assertEqual(len(subject["nonce"]), 64)
        production = [c for c in calls if "--certificate-identity" in c][-1]
        self.assertEqual(production[production.index("--certificate-identity") + 1],
                         release.DEFAULT_TRUSTED_IDENTITY.format(version="v0.0.0"))

    def test_unreviewed_fork_tag_and_pr_are_rejected_before_signing(self):
        step = self.job["steps"][0]
        for key, value in (("GITHUB_REPOSITORY", "fork/repo"), ("GITHUB_REF", "refs/tags/v1.60.0"),
                           ("REF_PROTECTED", "false"), ("GITHUB_EVENT_NAME", "pull_request")):
            result = release.run_step(step, dict(self.env, **{key: value}), ROOT, self.scratch)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(release.calls(self.log), [])


class NonPublishingWorkflowTests(unittest.TestCase):
    def test_pins_permissions_and_no_production_credentials_or_calls(self):
        for path in (SMOKE, PROOF):
            doc = release.load(path)
            self.assertEqual(doc["permissions"], {"contents": "read"})
            for key, value in release.walk(doc):
                if key == "uses":
                    if value.startswith("./"):
                        self.assertEqual(value, "./.github/actions/setup-helm-tools")
                    else:
                        self.assertRegex(value, release.PINNED_ACTION_RE)
            for name, job in doc["jobs"].items():
                expected = {"contents": "read", "id-token": "write"} if path == SMOKE and name == "keyless" else {"contents": "read"}
                self.assertEqual(job["permissions"], expected)
                for step in job["steps"]:
                    if "run" in step:
                        self.assertNotIn("${{", step["run"])
                    if str(step.get("uses", "")).startswith("actions/checkout@"):
                        self.assertEqual(step["with"]["persist-credentials"], "false")
            text = path.read_text()
            for token in ("${{ secrets.", "gh release upload", "gh release create", "docker login", "docker push", "git push",
                          "--tlog-upload=false", "--certificate-identity-regexp", "pull_request_target:"):
                self.assertNotIn(token, text)
        self.assertNotIn("actions/upload-artifact@", SMOKE.read_text())

    def test_smoke_oidc_only_on_protected_canonical_dev_manual_run(self):
        doc = release.load(SMOKE)
        self.assertEqual(set(doc["on"]), {"pull_request", "workflow_dispatch"})
        condition = doc["jobs"]["keyless"]["if"]
        for part in ("github.event_name == 'workflow_dispatch'", "github.repository == 'future-agi/future-agi'",
                     "github.ref == 'refs/heads/dev'", "github.ref_protected"):
            self.assertIn(part, condition)
        self.assertEqual(doc["jobs"]["keyless"]["needs"], "contracts")
        identity = doc["jobs"]["keyless"]["env"]["CERT_IDENTITY"]
        self.assertEqual(identity, "https://github.com/future-agi/future-agi/.github/workflows/release-signing-smoke.yml@refs/heads/dev")
        self.assertNotEqual(identity, release.DEFAULT_TRUSTED_IDENTITY.format(version="v0.0.0"))
        self.assertIn("PUBLIC TRANSPARENCY", SMOKE.read_text())
        self.assertIn("CI-SMOKE-ONLY-NOT-A-RELEASE", SMOKE.read_text())

    def test_proof_workflow_executes_rehearsal_on_same_frozen_candidate(self):
        doc = release.load(PROOF)
        job = doc["jobs"]["proof"]
        download = release.one_step(job, lambda s: str(s.get("uses", "")).startswith("actions/download-artifact@"))
        self.assertEqual(download["with"], {"name": "update-candidate", "path": "${{ runner.temp }}/candidate"})
        run = release.one_step(job, release.runs_containing("scripts/build_update_compatibility_proof.py"))["run"]
        self.assertIn('--run-id "$GITHUB_RUN_ID"', run)
        self.assertIn('--policy "$RUNNER_TEMP/evidence/policy.json"', run)
        self.assertLess(run.index('scripts/rehearse_update_compatibility.py'),
                        run.index('scripts/build_update_compatibility_proof.py'))
        self.assertEqual(run.count('--target "$RUNNER_TEMP/candidate/future-agi-update-manifest.json"'), 2)
        self.assertIn("github.event_name != 'pull_request'", job["if"])
        self.assertNotIn("continue-on-error", PROOF.read_text())
        self.assertNotIn("waiver:", PROOF.read_text())
        contracts = doc['jobs']['contracts']
        tests = release.one_step(contracts,release.runs_containing('python3 -m unittest discover'))
        self.assertIn('python3 -m unittest deploy.tests.update_e2e.test_automatic_publisher -v',tests['run'])
        verifier = release.one_step(contracts,lambda s:s.get('uses')=='./.github/actions/setup-helm-tools')
        self.assertEqual(verifier['with']['cosign'],'true')


class ProofPublishTests(release._StepHarness):
    def setUp(self):
        super().setUp()
        release.write_fake(self.bin, "gh", release.FAKE_GH)
        release.write_fake(self.bin, "cosign", release.FAKE_COSIGN)
        self.release = self.tmp / "release"
        self.release.mkdir()
        self.runner = self.tmp / "runner"
        (self.runner / "new").mkdir(parents=True)
        self.manifest = b'{"version":"v1.60.0"}'
        (self.runner / "new" / release.MANIFEST_ASSET).write_bytes(self.manifest)
        self.summary = self.tmp / "summary.txt"
        self.signer = release.DEFAULT_TRUSTED_IDENTITY.format(version=self.version)

    run_publish = release.PublishStepTests.run_publish
    bundle_for = release.PublishStepTests.bundle_for
    uploads = release.PublishStepTests.uploads
    assets = release.PublishStepTests.assets

    def test_proof_precedes_bundle_and_manifest_and_rerun_preserves_all(self):
        (self.runner / "new" / PROOF_ASSET).write_bytes(b"synthetic proof already validated by preceding step")
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([Path(c[4]).name for c in self.uploads()], [PROOF_ASSET, release.BUNDLE_ASSET, release.MANIFEST_ASSET])
        before = self.assets()
        self.log.unlink()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(before, self.assets())

    def test_different_published_proof_never_replaced_or_manifest_attached(self):
        (self.runner / "new" / PROOF_ASSET).write_bytes(b"new")
        (self.release / PROOF_ASSET).write_bytes(b"old")
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.assets(), {PROOF_ASSET: b"old"})
        self.assertEqual(self.uploads(), [])

    def test_missing_proof_for_existing_manifest_fails_closed(self):
        (self.runner / "new" / PROOF_ASSET).write_bytes(b"new")
        (self.release / release.MANIFEST_ASSET).write_bytes(self.manifest)
        (self.release / release.BUNDLE_ASSET).write_text(self.bundle_for(self.manifest))
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing its pinned proof", result.stdout)
        self.assertEqual(self.uploads(), [])


if __name__ == "__main__":
    unittest.main()
