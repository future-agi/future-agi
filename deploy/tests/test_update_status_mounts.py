"""Optional status mounts: daemon-free Compose and host-only installer actions.

No Docker daemon is contacted here. Real mount qualification is a separate,
explicit dedicated-host lane; unit sweeps must never start a stack.
"""

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SLOT = "a" * 32
TARGET = "/run/futureagi-host-update"
OVERLAYS = {
    "standalone": "deploy/docker-compose.host-status.standalone.yml",
    "distributed": "deploy/docker-compose.host-status.distributed.yml",
}


def clean_environment():
    return {key: value for key, value in os.environ.items() if key in ("PATH", "HOME")}


class StatusPreparationTests(unittest.TestCase):
    def setUp(self):
        # /tmp and macOS /var are symlinks or writable ancestors, rightly refused.
        temporary = tempfile.TemporaryDirectory(prefix="fagi-status-unit-", dir=Path.home())
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "checkout"
        (self.root / "bin").mkdir(parents=True)
        (self.root / "deploy").mkdir()
        for name in ("bin/install", "deploy/setup.sh"):
            shutil.copyfile(ROOT / name, self.root / name)
        self.parent = self.directory / "public"
        self.parent.mkdir(mode=0o755)
        self.source = self.parent / SLOT
        self.executables = self.directory / "executables"
        self.executables.mkdir()
        (self.executables / "python3").symlink_to(sys.executable)
        self.docker_marker = self.directory / "docker-called"
        docker = self.executables / "docker"
        docker.write_text('#!/bin/sh\ntouch "$DOCKER_MARKER"\nexit 91\n')
        docker.chmod(0o755)
        # Retained files must be byte-for-byte unchanged by these early actions.
        for name in (".env", "deploy/.env.production"):
            (self.root / name).write_text("SECRET_KEY=retained-do-not-touch\n")
        self.environment = {
            **clean_environment(),
            "PATH": str(self.executables) + os.pathsep + os.defpath,
            "DOCKER_MARKER": str(self.docker_marker),
            "FI_HOST_UPDATE_STATUS_DIR": str(self.source),
            "FI_HOST_UPDATE_STATUS_SLOT": SLOT,
        }

    def run_action(self, *args, script="bin/install", changes=None):
        result = subprocess.run(
            ["/bin/bash", str(self.root / script), *args],
            cwd=self.directory,
            env={**self.environment, **(changes or {})},
            capture_output=True, text=True, timeout=10,
        )
        self.assertFalse(self.docker_marker.exists(), "host action contacted Docker")
        self.assertFalse(list(self.root.glob("install-*.log")))
        for name in (".env", "deploy/.env.production"):
            self.assertEqual((self.root / name).read_text(), "SECRET_KEY=retained-do-not-touch\n")
        self.assertNotIn("retained-do-not-touch", result.stdout + result.stderr)
        return result

    def test_help_is_read_only_for_both_entrypoints(self):
        for script in ("bin/install", "deploy/setup.sh"):
            with self.subTest(script=script):
                result = self.run_action("--host-status-help", script=script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Directory preparation alone is NOT enrollment", result.stdout)
                self.assertIn("config --quiet", result.stdout)
                self.assertIn("stage, separately approved initialization, then apply", result.stdout)
                self.assertIn("schedule init --docker-context NAME --status-overlay", result.stdout)
                self.assertIn("--status-overlay takes NO path", result.stdout)
                self.assertIn("Adding/removing the overlay requires", result.stdout)
                self.assertIn("exactly these three ordered files", result.stdout)
                self.assertFalse(self.source.exists())

    def test_fresh_directory_is_operator_owned_755_even_with_private_umask(self):
        previous = os.umask(0o077)
        try:
            result = self.run_action("--prepare-host-status")
        finally:
            os.umask(previous)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source.stat().st_uid, os.getuid())
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(self.source.iterdir()), [])
        self.assertIn("No registration", result.stdout)

    def test_production_prepare_does_not_initialize_or_boot(self):
        result = self.run_action("--prepare-host-status", script="deploy/setup.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o755)

    def test_existing_source_is_never_repaired(self):
        self.source.mkdir(mode=0o700)
        canary = self.source / "status.json"
        canary.write_bytes(b"unchanged")
        before = self.source.stat()
        for script in ("bin/install", "deploy/setup.sh"):
            result = self.run_action("--prepare-host-status", script=script)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("left unchanged", result.stderr)
            self.assertEqual(self.source.stat(), before)
            self.assertEqual(canary.read_bytes(), b"unchanged")

    def test_combined_flags_never_enter_install_flow(self):
        for script in ("bin/install", "deploy/setup.sh"):
            for option in ("--host-status-help", "--prepare-host-status"):
                result = self.run_action(option, "--confirm-initialized", script=script)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.source.exists())

    def test_missing_parent_is_not_implicitly_created(self):
        self.parent.rmdir()
        result = self.run_action("--prepare-host-status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("trusted operator-owned public parent", result.stderr)
        self.assertFalse(self.parent.exists())

    def test_untrusted_ancestor_and_symlink_are_refused(self):
        self.parent.chmod(0o777)
        result = self.run_action("--prepare-host-status")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.source.exists())
        self.parent.chmod(0o755)
        alias = self.directory / "alias"
        alias.symlink_to(self.parent, target_is_directory=True)
        result = self.run_action("--prepare-host-status", changes={
            "FI_HOST_UPDATE_STATUS_DIR": str(alias / SLOT),
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.source.exists())
        self.source.symlink_to(self.parent, target_is_directory=True)
        result = self.run_action("--prepare-host-status")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.source.is_symlink())

    def test_invalid_identity_paths_and_sudo_are_refused(self):
        cases = [
            {"FI_HOST_UPDATE_STATUS_SLOT": ""},
            {"FI_HOST_UPDATE_STATUS_SLOT": "A" * 32},
            {"FI_HOST_UPDATE_STATUS_DIR": ""},
            {"FI_HOST_UPDATE_STATUS_DIR": "relative/" + SLOT},
            {"FI_HOST_UPDATE_STATUS_DIR": str(self.parent / "wrong-slot")},
            {"FI_HOST_UPDATE_STATUS_DIR": str(self.parent) + "/../" + SLOT},
            {"FI_HOST_UPDATE_STATUS_DIR": str(self.root / SLOT)},
            {"FI_HOST_UPDATE_STATUS_DIR": str(self.directory / ".futureagi-updates" / SLOT)},
            {"SUDO_UID": "1000"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                result = self.run_action("--prepare-host-status", changes=changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.source.exists())


class StatusOverlayStaticTests(unittest.TestCase):
    def test_overlay_inventory_and_ci_filters(self):
        inventory = (ROOT / "deploy/update/deployment-files.txt").read_text().splitlines()
        ci = (ROOT / ".github/workflows/update-ci.yml").read_text()
        for overlay in OVERLAYS.values():
            self.assertEqual(ci.count('      - "' + overlay + '"'), 2)
            self.assertIn(overlay, inventory)
        for base in ("docker-compose.yml", "docker-compose.distributed.yml", "deploy/docker-compose.production.yml"):
            self.assertNotIn("FI_HOST_UPDATE_STATUS", (ROOT / base).read_text())

    def test_reader_and_overlay_fixed_path_agree(self):
        spec = importlib.util.spec_from_file_location(
            "mount_reader", ROOT / "futureagi/tfc/host_updates/status.py"
        )
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        self.assertEqual(reader.STATUS_FILE, TARGET + "/status.json")
        result = reader.read_public_status(configured_file="", expected_installation=None)
        self.assertEqual(result["result"]["availability"], "unavailable")
        self.assertEqual(result["result"]["reason_code"], "not_configured")
        self.assertIsNone(result["result"]["snapshot"])
        for overlay in OVERLAYS.values():
            self.assertIn("FI_HOST_UPDATE_STATUS_FILE: " + reader.STATUS_FILE, (ROOT / overlay).read_text())

    def test_reference_covers_all_overlay_inputs(self):
        reference = (ROOT / "deploy/env-reference.toml").read_text()
        keys = {"FI_HOST_UPDATE_STATUS_FILE"}
        for overlay in OVERLAYS.values():
            keys.update(re.findall(r"\$\{([A-Z_][A-Z_0-9]*)", (ROOT / overlay).read_text()))
        self.assertEqual(keys, {"FI_HOST_UPDATE_STATUS_DIR", "FI_HOST_UPDATE_STATUS_SLOT", "FI_HOST_UPDATE_STATUS_FILE"})
        for key in keys:
            self.assertIn('keys = ["' + key + '"]', reference)


class StatusMountAssertions:
    def assert_status_mounts(self, mounts, source):
        self.assertEqual(len(mounts), 1)
        mount = copy.deepcopy(mounts[0])
        self.assertIs(mount.get("read_only"), True)
        self.assertIsInstance(mount.get("bind"), dict)
        # Compose v2 serializes the false bool's zero value as bind: {}, while
        # newer releases retain create_host_path: false. Neither creates a
        # missing host source. Do not normalize true or any other bind option.
        self.assertIs(mount["bind"].pop("create_host_path", False), False)
        self.assertEqual(mount, {
            "type": "bind", "source": str(source), "target": TARGET,
            "read_only": True, "bind": {},
        })


class StatusMountSemanticsTests(StatusMountAssertions, unittest.TestCase):
    def setUp(self):
        self.mount = {"type": "bind", "source": "/owned/public/slot", "target": TARGET,
                      "read_only": True, "bind": {}}

    def test_absent_and_explicit_false_have_the_same_no_create_meaning(self):
        for bind in ({}, {"create_host_path": False}):
            with self.subTest(bind=bind):
                self.assert_status_mounts([{**self.mount, "bind": bind}], self.mount["source"])

    def test_create_writable_wrong_target_and_other_mount_changes_are_rejected(self):
        for changes in ({"bind": {"create_host_path": True}}, {"read_only": False},
                        {"target": "/wrong"}, {"source": "/wrong"}, {"type": "volume"},
                        {"bind": {"propagation": "shared"}}, {"bind": None}):
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                self.assert_status_mounts([{**self.mount, **changes}], self.mount["source"])
        for mounts in ([], [self.mount, self.mount]):
            with self.subTest(mounts=mounts), self.assertRaises(AssertionError):
                self.assert_status_mounts(mounts, self.mount["source"])

    def test_source_overlays_explicitly_disable_host_path_creation(self):
        for overlay in OVERLAYS.values():
            with self.subTest(overlay=overlay):
                self.assertIn("\n        bind:\n          create_host_path: false\n", (ROOT / overlay).read_text())


@unittest.skipUnless(shutil.which("docker"), "Docker CLI required for daemon-free Compose rendering")
class StatusOverlayComposeTests(StatusMountAssertions, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fagi-compose-status-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.source = self.directory / SLOT
        self.environment = clean_environment()
        # Supply only test data for production's explicit interpolation requirements.
        production = (ROOT / "deploy/docker-compose.production.yml").read_text()
        for key in re.findall(r"\$\{([A-Z_0-9]+):\?", production):
            self.environment[key] = "s08-test"
        self.environment.update({
            "FI_HOST_UPDATE_STATUS_DIR": str(self.source),
            "FI_HOST_UPDATE_STATUS_SLOT": SLOT,
        })

    def render(self, topology, *, production=False, overlay=False, missing=None,
               extra_env=None, env_file=None, clear_keys=()):
        base = "docker-compose.yml" if topology == "standalone" else "docker-compose.distributed.yml"
        args = ["docker", "compose", "--env-file", str(env_file or os.devnull), "-f", str(ROOT / base)]
        if production:
            args += ["-f", str(ROOT / "deploy/docker-compose.production.yml")]
        if overlay:
            args += ["-f", str(ROOT / OVERLAYS[topology])]
        args += ["config", "--format", "json"]
        env = {**self.environment, **(extra_env or {})}
        for key in clear_keys:
            env.pop(key, None)
        if missing:
            env.pop(missing, None)
        result = subprocess.run(args, env=env, cwd=ROOT, capture_output=True, text=True, timeout=30)
        if missing and overlay:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(missing, result.stderr)
            return None
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_only_intended_service_changes_and_default_model_is_unchanged(self):
        for topology, production in (("standalone", False), ("distributed", False), ("distributed", True)):
            with self.subTest(topology=topology, production=production):
                baseline = self.render(topology, production=production)
                absent = self.render(topology, production=production, missing="FI_HOST_UPDATE_STATUS_DIR")
                self.assertEqual(baseline, absent)
                rendered = self.render(topology, production=production, overlay=True)
                original = copy.deepcopy(rendered)
                consumer = "app" if topology == "standalone" else "backend"
                service = rendered["services"][consumer]
                self.assertEqual(service["environment"].pop("FI_HOST_UPDATE_STATUS_FILE"), TARGET + "/status.json")
                self.assertEqual(service["environment"].pop("FI_HOST_UPDATE_STATUS_SLOT"), SLOT)
                mounts = [v for v in service["volumes"] if v["target"] == TARGET]
                self.assert_status_mounts(mounts, self.source)
                service["volumes"].remove(mounts[0])
                if not service["volumes"] and "volumes" not in baseline["services"][consumer]:
                    del service["volumes"]
                self.assertEqual(rendered, baseline)
                for name, other in original["services"].items():
                    self.assertNotIn("FI_HOST_UPDATE_STATUS_DIR", other.get("environment", {}))
                    if name != consumer:
                        self.assertNotIn("FI_HOST_UPDATE_STATUS_SLOT", other.get("environment", {}))
                        self.assertNotIn(TARGET, [v["target"] for v in other.get("volumes", [])])
                self.assertFalse(self.source.exists(), "config created host source")

    def test_profiled_workers_and_sandboxes_never_receive_status(self):
        for topology in OVERLAYS:
            rendered = self.render(topology, overlay=True, extra_env={"COMPOSE_PROFILES": "*"})
            consumer = "app" if topology == "standalone" else "backend"
            for name, service in rendered["services"].items():
                if name == consumer:
                    continue
                self.assertNotIn("FI_HOST_UPDATE_STATUS_FILE", service.get("environment", {}))
                self.assertNotIn("FI_HOST_UPDATE_STATUS_SLOT", service.get("environment", {}))
                self.assertNotIn(TARGET, [v["target"] for v in service.get("volumes", [])])

    def test_missing_required_source_or_slot_fails_closed(self):
        for topology in OVERLAYS:
            for key in ("FI_HOST_UPDATE_STATUS_DIR", "FI_HOST_UPDATE_STATUS_SLOT"):
                self.render(topology, overlay=True, missing=key)

    def test_production_env_file_selects_slot_and_shell_precedence_is_explicit(self):
        selected = self.directory / "production.env"
        file_slot = "b" * 32
        file_source = self.directory / file_slot
        selected.write_text(
            "FI_HOST_UPDATE_STATUS_DIR=" + str(file_source) + "\n"
            + "FI_HOST_UPDATE_STATUS_SLOT=" + file_slot + "\n"
        )
        keys = ("FI_HOST_UPDATE_STATUS_DIR", "FI_HOST_UPDATE_STATUS_SLOT")
        from_file = self.render("distributed", production=True, overlay=True,
                                env_file=selected, clear_keys=keys)
        backend = from_file["services"]["backend"]
        self.assertEqual(backend["environment"]["FI_HOST_UPDATE_STATUS_SLOT"], file_slot)
        self.assertEqual(next(v["source"] for v in backend["volumes"] if v["target"] == TARGET), str(file_source))
        from_shell = self.render("distributed", production=True, overlay=True, env_file=selected)
        self.assertEqual(from_shell["services"]["backend"]["environment"]["FI_HOST_UPDATE_STATUS_SLOT"], SLOT)

    def test_explicit_production_order_ignores_ambient_compose_file(self):
        baseline = self.render("distributed", production=True, overlay=True)
        actual = self.render("distributed", production=True, overlay=True, extra_env={
            "COMPOSE_FILE": str(self.directory / "does-not-exist.yml"),
        })
        self.assertEqual(baseline, actual)
        backend = actual["services"]["backend"]
        self.assertEqual(backend["environment"]["ENV_TYPE"], "production")


if __name__ == "__main__":
    unittest.main()
