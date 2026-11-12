"""Offline Hub profile contracts; read Compose source, never dotenv or Docker.

Run with Python + PyYAML: python -m unittest discover -s deploy/tests
-p test_distributed_hub_profile.py. Installer probes execute only extracted
functions, with an in-memory env provider and a shell Compose stand-in.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HUB = {"hub-checker", "hub-runner"}
# Canonical parsed service dictionaries at faeb60d01c726bd43e4a57abe0f02a3470264301.
# Only the new profiles field may differ. No git history is needed to run this test.
BASE_SERVICE_SHA256 = {
    "hub-checker": "dbaf95b06aebcb1b67cb795618408e820919f632afb81338583cb22098f530ab",
    "hub-runner": "b7041a0fa06f30e1b944e2c20434be1443da56bf16d4c27223ece68467ca0e73",
}


def services(filename="docker-compose.distributed.yml"):
    return yaml.safe_load((ROOT / filename).read_text())["services"]


def active_services(model, profiles=()):
    """Compose's service-profile filter, without interpolation or file includes."""
    enabled = set(profiles)
    return {
        name: service
        for name, service in model.items()
        if not service.get("profiles")
        or "*" in enabled
        or enabled.intersection(service["profiles"])
    }


def shell_function(text, name):
    match = re.search(rf"(?ms)^{name}\(\)\s*\{{.*?^\}}", text)
    if match is None:
        raise AssertionError(f"Missing installer function: {name}")
    return match.group()


def mapping_value(node, key):
    return next(value for name, value in node.value if name.value == key)


class DistributedHubProfileTests(unittest.TestCase):
    def test_default_has_no_hub_services(self):
        self.assertFalse(HUB.intersection(active_services(services())))

    def test_default_has_no_privileged_or_host_userns_services(self):
        offenders = [
            name
            for name, service in active_services(services()).items()
            if service.get("privileged") is True or service.get("userns_mode") == "host"
        ]
        self.assertEqual(offenders, [])

    def test_both_services_use_exactly_the_standalone_hub_profile(self):
        standalone = services("docker-compose.yml")
        for name in sorted(HUB):
            with self.subTest(service=name):
                self.assertEqual(standalone[name]["profiles"], ["hub"])
                self.assertEqual(services()[name].get("profiles"), ["hub"])

    def test_hub_opt_in_restores_both_original_service_definitions(self):
        selected = active_services(services(), ["hub"])
        self.assertTrue(HUB.issubset(selected))
        for name in sorted(HUB):
            with self.subTest(service=name):
                definition = {k: v for k, v in selected[name].items() if k != "profiles"}
                canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
                self.assertEqual(
                    hashlib.sha256(canonical.encode()).hexdigest(),
                    BASE_SERVICE_SHA256[name],
                    f"{name}: a non-profile setting changed from the pinned base",
                )

    def test_unrelated_profiles_do_not_enable_the_hub(self):
        for profiles in (["all"], ["full"], ["peerdb"], ["ml", "sandbox"]):
            with self.subTest(profiles=profiles):
                self.assertFalse(HUB.intersection(active_services(services(), profiles)))

    def test_every_default_dependency_is_active(self):
        selected = active_services(services())
        for name, service in selected.items():
            with self.subTest(service=name):
                self.assertFalse(set(service.get("depends_on", {})) - selected.keys())
                for link in service.get("links", []):
                    self.assertIn(link.split(":", 1)[0], selected)
                for mount in service.get("volumes_from", []):
                    if not mount.startswith("container:"):
                        self.assertIn(mount.split(":", 1)[0], selected)

    def test_hub_dependencies_are_active_when_selected(self):
        selected = active_services(services(), ["hub"])
        for name in sorted(HUB):
            with self.subTest(service=name):
                self.assertFalse(set(selected[name].get("depends_on", {})) - selected.keys())

    def test_production_keeps_external_checker_without_local_runner(self):
        # Inspect Compose's tagged overrides as YAML nodes, not an invented
        # generic merge implementation: an empty ordinary list does NOT reset
        # the inherited profile list under Compose's merge rules.
        document = yaml.compose((ROOT / "deploy/docker-compose.production.yml").read_text())
        overlay = mapping_value(document, "services")
        checker = mapping_value(overlay, "hub-checker")
        fields = {key.value: value for key, value in checker.value}
        self.assertIn("profiles", fields, "production must reset the inherited hub profile")
        self.assertEqual(fields["profiles"].tag, "!reset")
        self.assertEqual(fields["profiles"].value, [])
        self.assertEqual(fields["depends_on"].tag, "!override")
        self.assertEqual([key.value for key, _ in fields["depends_on"].value], ["backend"])
        runner_profiles = mapping_value(mapping_value(overlay, "hub-runner"), "profiles")
        self.assertEqual(runner_profiles.tag, "!override", "must not merge in the base hub profile")
        self.assertEqual([node.value for node in runner_profiles.value], ["not-in-production"])


class InstallerHubProfileTests(unittest.TestCase):
    def probe(self, shell_profiles, file_profiles, distributed):
        installer = (ROOT / "bin/install").read_text()
        self.assertIn('. "$ROOT/bin/lib/env.sh"', installer)
        selected_profiles = shell_profiles if shell_profiles is not None else file_profiles
        model = active_services(
            services("docker-compose.distributed.yml" if distributed else "docker-compose.yml"),
            selected_profiles.split(","),
        )
        # The stub checks the profile arriving at the real $DC call before
        # returning synthetic config. It never invokes a Docker binary.
        rendered = yaml.safe_dump({"services": {name: {"image": "fixture/" + name} for name in model}})
        script = "\n".join([
            "set -euo pipefail",
            (ROOT / "bin/lib/env.sh").read_text(),
            'env_text() { [[ "$1" == COMPOSE_PROFILES ]] && printf "%s" "$FILE_PROFILES"; }',
            'set_env_var() { [[ "$1" == COMPOSE_FILE ]] || return 91; export COMPOSE_FILE="$2"; }',
            shell_function(installer, "record_mode"),
            shell_function(installer, "compose_service_images"),
            'new_compose_setting="$EXPECTED_FILE"',
            "record_mode",
            '[[ "$COMPOSE_FILE" == "$EXPECTED_FILE" ]]',
            '[[ -z "$new_compose_setting" ]]',
            'compose_probe() {',
            '  [[ "$1" == config && "$#" == 1 ]] || return 92',
            '  [[ "$(compose_env_value COMPOSE_PROFILES)" == "$EXPECTED_PROFILES" ]] || return 93',
            '  printf "%s" "$SYNTHETIC_CONFIG"',
            '}',
            "DC=compose_probe",
            "compose_service_images",
        ])
        env = {
            "PATH": os.defpath,
            "FILE_PROFILES": file_profiles,
            "EXPECTED_PROFILES": selected_profiles,
            "EXPECTED_FILE": "docker-compose.distributed.yml" if distributed else "docker-compose.yml",
            "SYNTHETIC_CONFIG": rendered,
        }
        if shell_profiles is not None:
            env["COMPOSE_PROFILES"] = shell_profiles
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["/bin/bash", "-c", script], cwd=directory, env=env,
                capture_output=True, text=True, timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return {line.split("\t", 1)[0] for line in result.stdout.splitlines()}

    def test_shell_hub_selection_survives_mode_recording_and_compose_call(self):
        for distributed in (False, True):
            with self.subTest(distributed=distributed):
                self.assertTrue(HUB.issubset(self.probe("ml,hub", "sandbox", distributed)))

    def test_file_hub_selection_uses_the_same_path_for_both_topologies(self):
        for distributed in (False, True):
            with self.subTest(distributed=distributed):
                self.assertTrue(HUB.issubset(self.probe(None, "hub", distributed)))

    def test_installer_default_does_not_enumerate_hub_images(self):
        for distributed in (False, True):
            with self.subTest(distributed=distributed):
                self.assertFalse(HUB.intersection(self.probe(None, "", distributed)))


if __name__ == "__main__":
    unittest.main()
