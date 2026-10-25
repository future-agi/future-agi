"""Daemon-free package.sh regressions on a temporary copy of the actual chart.

Uses real Helm when installed; otherwise a deliberately limited fake implements
only the package/template CLI boundary exercised here. The fake is not a Helm
render/smoke qualification. Packaging requires Python with PyYAML, as in CI; set
PYTHON to that interpreter when the unittest interpreter lacks that dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/helm/futureagi"

# The fallback preserves the appVersion/tag/digest selection in _helpers.tpl for
# the product components used by these tests, and packages the real input files.
# It cannot stand in for the independent real Helm source/package/kind CI gates.
FAKE_HELM = r'''
import json, sys, tarfile
from pathlib import Path
import yaml
args = sys.argv[1:]
if args[0] == 'package':
    assert len(args) == 4 and args[2] == '--destination', args
    chart = Path(args[1])
    data = yaml.safe_load((chart / 'Chart.yaml').read_text())
    output = Path(args[3]) / ('futureagi-' + data['version'] + '.tgz')
    with tarfile.open(output, 'w:gz') as archive:
        archive.add(chart, arcname='futureagi')
    print('Successfully packaged chart and saved it to: ' + str(output))
elif args[0] == 'template':
    assert args[1] == 'futureagi' and args[3:5] == ['--namespace', 'futureagi'], args
    chart = Path(args[2])
    meta = yaml.safe_load((chart / 'Chart.yaml').read_text())
    values = yaml.safe_load((chart / 'values.yaml').read_text())
    schema = json.loads((chart / 'values.schema.json').read_text())
    images = []
    for key in schema['properties']['image']['properties']['digests']['properties']:
        image = values[key]['image']
        registry = image.get('registry') or values['image'].get('registry') or 'docker.io'
        tag = image.get('tag') or values['image'].get('tag') or meta['appVersion']
        ref = registry.rstrip('/') + '/' + image['repository'] + ':' + tag
        digest = values['image']['digests'].get(key)
        if digest and tag == meta['appVersion'] and values['image']['pinDigests']:
            ref += '@' + digest
        images.append({'name': key, 'image': ref})
    print(yaml.safe_dump({'apiVersion': 'v1', 'kind': 'Pod', 'spec': {'containers': images}}))
else:
    raise AssertionError(args)
'''


class HelmPackagePrereleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="helm-package-prerelease-")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.chart = self.tmp / "chart"
        shutil.copytree(CHART, self.chart)
        self.destination = self.tmp / "packages"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.python = os.environ.get("PYTHON", sys.executable)
        # package.sh stamps digests through PyYAML in this interpreter.
        probe = subprocess.run([self.python, "-I", "-c", "import yaml"], capture_output=True, timeout=30)
        self.has_yaml = probe.returncode == 0
        self.helm = shutil.which("helm")
        if self.helm is None:
            fake = self.bin / "helm"
            fake.write_text("#!" + self.python + "\n" + FAKE_HELM)
            fake.chmod(0o755)
            self.helm = str(fake)
        self.env = dict(os.environ, PYTHON=self.python, HELM=self.helm,
                        PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                        HOME=str(self.tmp), TMPDIR=str(self.tmp), PYTHONDONTWRITEBYTECODE="1")
        # No accidentally introduced registry/daemon access is permitted.
        self.forbidden_log = self.tmp / "forbidden.log"
        self.env["FORBIDDEN_LOG"] = str(self.forbidden_log)
        for tool in ("docker", "crane", "oras", "curl", "wget"):
            path = self.bin / tool
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$FORBIDDEN_LOG"\nexit 99\n')
            path.chmod(0o755)
        schema = json.loads((self.chart / "values.schema.json").read_text())
        keys = schema["properties"]["image"]["properties"]["digests"]["properties"]
        self.digests = {key: "sha256:" + hashlib.sha256(key.encode()).hexdigest() for key in keys}
        self.digest_file = self.tmp / "digests.txt"
        self.digest_file.write_text("".join(key + "=" + digest + "\n" for key, digest in self.digests.items()))

    def stamp_version(self, version, app_version=None):
        path = self.chart / "Chart.yaml"
        text = path.read_text()
        # JSON string quoting is valid YAML, including malformed-version fixtures.
        for key, value in (("version", version), ("appVersion", app_version if app_version is not None else "v" + version)):
            text = re.sub(r"^" + key + r":.*$", lambda match: key + ": " + json.dumps(value), text, flags=re.M)
        path.write_text(text)

    def need_yaml(self):
        if not self.has_yaml:
            self.skipTest("packaging interpreter has no PyYAML; set PYTHON to one that does")

    def run_package(self, expected=None, *, no_digests=False):
        command = ["bash", str(self.chart / "hack/package.sh"), "--destination", str(self.destination)]
        command += ["--no-digests"] if no_digests else ["--digests-file", str(self.digest_file)]
        if expected is not None:
            command += ["--version", expected]
        result = subprocess.run(command, cwd=str(self.tmp), env=self.env, capture_output=True, text=True, timeout=120)
        self.assertFalse(self.forbidden_log.exists(), "package attempted network/daemon access")
        return result

    def yaml_json(self, raw):
        # Exercise real PyYAML in the packaging interpreter, not a fake YAML parser.
        result = subprocess.run([self.python, "-c", "import json, sys, yaml; print(json.dumps(yaml.safe_load(sys.stdin.read())))"],
                                input=raw, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + " (set PYTHON to an interpreter with PyYAML)")
        return json.loads(result.stdout)

    def assert_package(self, version, expected=None, *, no_digests=False):
        self.need_yaml()
        self.stamp_version(version)
        before = {p.relative_to(self.chart): p.read_bytes() for p in self.chart.rglob("*") if p.is_file()}
        result = self.run_package(expected, no_digests=no_digests)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        package = self.destination / ("futureagi-" + version + ".tgz")
        self.assertEqual(result.stdout.strip(), str(package))
        self.assertTrue(package.is_file())
        with tarfile.open(package, "r:gz") as archive:
            chart = self.yaml_json(archive.extractfile("futureagi/Chart.yaml").read().decode())
            values = self.yaml_json(archive.extractfile("futureagi/values.yaml").read().decode())
            self.assertEqual(archive.extractfile("futureagi/values.schema.json").read(),
                             before[Path("values.schema.json")])
        self.assertEqual(chart["version"], version)
        self.assertEqual(chart["appVersion"], "v" + version)
        self.assertEqual(values["image"]["digests"], {} if no_digests else self.digests)
        annotations = self.yaml_json(chart["annotations"]["artifacthub.io/images"])
        result = subprocess.run(["bash", str(self.chart / "hack/list-images.sh"), str(package)],
                                cwd=str(self.tmp), env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        lock = set(result.stdout.splitlines())
        self.assertEqual(lock, {image["image"] for image in annotations})
        for key, digest in self.digests.items():
            image = values[key]["image"]
            repo = (image.get("registry") or values["image"].get("registry") or "docker.io").rstrip('/')
            ref = repo + '/' + image["repository"] + ':v' + version
            if not no_digests:
                ref += '@' + digest
            self.assertIn(ref, lock)
        self.assertEqual(before, {p.relative_to(self.chart): p.read_bytes() for p in self.chart.rglob("*") if p.is_file()},
                         "packaging must change only its temporary copy")
        self.assertFalse(self.forbidden_log.exists())

    def test_stable_package_and_prefixed_expected_tag(self):
        self.assert_package("1.60.0", "v1.60.0")

    def test_beta_package_and_unprefixed_expected_tag(self):
        self.assert_package("1.60.0-beta.12", "1.60.0-beta.12")

    def test_rc_package_and_prefixed_expected_tag(self):
        self.assert_package("1.60.0-rc.2", "v1.60.0-rc.2")

    def test_zero_prerelease_number_and_no_digest_mode(self):
        for version in ("0.0.0-beta.0", "1.60.0-rc.0"):
            with self.subTest(version=version):
                self.assert_package(version, no_digests=True)

    def test_release_integer_upper_bounds_are_accepted(self):
        version = "999999999999999999.999999999999999999.999999999999999999-beta.999999999999999999"
        self.assert_package(version, "v" + version, no_digests=True)

    def test_invalid_version_table_rejected_before_packaging(self):
        invalid = ["", "1.60", "v1.60.0", "-1.60.0", "1.60.0.1", "1.60.0-alpha.1",
                   "1.60.0-beta", "1.60.0-beta.", "1.60.0-beta.01", "1.60.0-rc.00",
                   "1.60.0-rc.-1", "1.60.0-rc.x", "1.60.0-RC.1", "1.60.0-Beta.1",
                   "01.60.0-beta.1", "1.060.0-beta.1", "1.60.00-rc.1",
                   "1.60.0-beta.1.2", "1.60.0-beta.1-foo", "1.60.0+build", "1.60.0-rc.1+build",
                   "1.60.0-beta.1000000000000000000", "1000000000000000000.60.0-rc.1",
                   "1.60.0/rc.1", "../1.60.0", "1.60.0:rc.1", "1.60.0@sha256:bad",
                   "1.60.0-rc.1;false", "1.60.0-rc.$(false)", "1.60.0-rc.1\n", "1.60.0-rc.1\t"]
        for version in invalid:
            with self.subTest(version=version):
                self.stamp_version(version)
                result = self.run_package()
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, r"package: Chart\.yaml version '.*' is not X\.Y\.Z")
                self.assertEqual(result.stdout, "")
                self.assertFalse(self.destination.exists())

    def test_app_version_must_exactly_match_chart_in_every_channel(self):
        for version in ("1.60.0", "1.60.0-beta.1", "1.60.0-rc.1"):
            for app_version in (version, "v1.59.0", "v1.60.0-beta.2"):
                with self.subTest(version=version, app_version=app_version):
                    self.stamp_version(version, app_version)
                    result = self.run_package()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("package: Chart.yaml appVersion '" + app_version + "' must be v" + version, result.stderr)
                    self.assertFalse(self.destination.exists())

    def test_expected_tag_must_match_exact_chart_channel_and_number(self):
        for version in ("1.60.0", "1.60.0-beta.1", "1.60.0-rc.1"):
            for expected in ("v1.59.0", "v1.60.0-beta.2", "v1.60.0-rc.2", "vv" + version):
                with self.subTest(version=version, expected=expected):
                    self.stamp_version(version)
                    result = self.run_package(expected)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("package: Chart.yaml has version " + version + ", not " + expected[1:], result.stderr)
                    self.assertFalse(self.destination.exists())

    def test_missing_digest_still_refuses_in_beta_lane(self):
        self.need_yaml()
        self.stamp_version("1.60.0-beta.1")
        self.digest_file.write_text("")
        result = self.run_package()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("package: no digest for ", result.stderr)
        self.assertEqual(list(self.destination.glob("*.tgz")), [])


if __name__ == "__main__":
    unittest.main()
