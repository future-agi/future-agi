"""bin/update adds the production Hub file exactly when the Omega Hub is enabled.

A production install's Compose arguments, the Compose command it prints for
the operator (compose_hint) and the files its containers' labels must name
(_ownership_files) list deploy/docker-compose.production.hub.yml right after
deploy/docker-compose.production.yml when COMPOSE_PROFILES lists `hub` or `*`
(each comma-separated entry stripped of spaces), and never otherwise.
COMPOSE_PROFILES is read as bin/update's own Compose commands see it: the env
file's value, else the environment's. No Docker is run: Compose and the Docker
CLI are recording stand-ins. The operator docs say how a production install
turns the Hub on: COMPOSE_PROFILES=hub in deploy/.env.production.
"""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update import install  # noqa: E402
from deploy.update.envfile import EnvFileError  # noqa: E402
from deploy.update.install import (  # noqa: E402
    DISTRIBUTED_FILE, PRODUCTION_ENV, PRODUCTION_OVERLAY, InstallError, Installation)

HUB_FILE = "deploy/docker-compose.production.hub.yml"
STATUS_FILE = "deploy/docker-compose.host-status.distributed.yml"
COMPOSE = ["docker", "compose"]
PROJECT = "fagi"
# COMPOSE_PROFILES values that enable the Omega Hub, and values that do not (None: unset).
ENABLED = ("hub", "ml,hub", "hub,sandbox", "sandbox,hub,ml", "ml, hub", "*", "ml,*")
NOT_ENABLED = (None, "", "hubx", "xhub", "hub-extra", "hub*", "**", "ml", "ml,sandbox", "full", ",")
# What every operator doc says, wherever its lines wrap.
HUB_SETTING = "`COMPOSE_PROFILES=hub` in `deploy/.env.production`"
# The env-reference rows whose production requirement depends on that setting.
HUB_ROWS = ("OMEGA_HUB_CHECKER_TOKEN", "OMEGA_HUB_APP_URL", "OMEGA_HUB_PUBLIC_API_URL")


def words(path):
    """The text of a repository file with every run of whitespace as one space."""
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def reference_row(key):
    """The deploy/env-reference.toml row whose keys are exactly [key], as text."""
    text = (ROOT / "deploy/env-reference.toml").read_text(encoding="utf-8")
    for block in text.split("[[part.section.row]]")[1:]:
        if f'keys = ["{key}"]' in block:
            return " ".join(block.split())
    raise AssertionError(f"deploy/env-reference.toml has no row for {key}")


def no_commands(args, **kwargs):
    raise AssertionError(f"ran {args}: choosing the Compose files runs no command")


def owning_docker(root, env_file, recorded):
    """Compose and the Docker CLI of one running backend whose labels name the `recorded` files."""
    cid = "c0ffee" + "0" * 58
    labels = {
        "com.docker.compose.project": PROJECT,
        "com.docker.compose.service": "backend",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.project.working_dir": str(root),
        "com.docker.compose.project.config_files": ",".join(str(root / name) for name in recorded),
        "com.docker.compose.project.environment_file": str(env_file),
    }
    record = {"Id": cid, "Image": "sha256:" + "a" * 64, "RestartCount": 0,
              "State": {"Status": "running", "ExitCode": 0},
              "Config": {"Image": "futureagi/future-agi:v1.0.0", "Entrypoint": None, "Cmd": None,
                         "Labels": labels}}
    model = {"name": PROJECT, "services": {"backend": {"image": "futureagi/future-agi:v1.0.0"}}}

    def run(args, **kwargs):
        if "config" in args:
            stdout = "backend\n" if "--services" in args else json.dumps(model)
        elif args[1:2] == ["ps"]:
            stdout = cid + "\n"
        elif args[1:2] == ["inspect"]:
            stdout = json.dumps([record])
        else:
            raise AssertionError(f"unexpected command {args}")
        return subprocess.CompletedProcess(args, 0, stdout, "")

    return run


class ProductionHubFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in (DISTRIBUTED_FILE, PRODUCTION_OVERLAY, HUB_FILE, STATUS_FILE):
            self.write(name, "services: {}\n")

    def write(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def production(self, file_profiles=None, environ_profiles=None, **kwargs):
        """A production install; each COMPOSE_PROFILES is set as given, or left unset (None)."""
        text = "FUTURE_AGI_VERSION=v1.0.0\n"
        if file_profiles is not None:
            text += f"COMPOSE_PROFILES={file_profiles}\n"
        self.write(PRODUCTION_ENV, text)
        environ = {} if environ_profiles is None else {"COMPOSE_PROFILES": environ_profiles}
        runner = kwargs.pop("runner", no_commands)
        return Installation(self.root, production=True, environ=environ, runner=runner,
                            compose=COMPOSE, **kwargs)

    def labelled(self, profiles, recorded):
        """A production install whose running backend's labels name the `recorded` files."""
        runner = owning_docker(self.root, self.root / PRODUCTION_ENV, recorded)
        return self.production(profiles, runner=runner)

    @staticmethod
    def files(hub, status=False):
        return ([DISTRIBUTED_FILE, PRODUCTION_OVERLAY] + ([HUB_FILE] if hub else [])
                + ([STATUS_FILE] if status else []))

    def expected_args(self, inst, hub, *args, status=False):
        words = [*COMPOSE, "--env-file", str(inst.env_file)]
        for name in self.files(hub, status):
            words += ["-f", str(self.root / name)]
        return [*words, *args]

    def expected_hint(self, hub, status=False):
        words = [*COMPOSE, "--env-file", PRODUCTION_ENV]
        for name in self.files(hub, status):
            words += ["-f", name]
        return " ".join(shlex.quote(word) for word in words)

    def assert_hub_file(self, inst, hub, status=False):
        self.assertEqual(inst.compose_args("config", "--quiet"),
                         self.expected_args(inst, hub, "config", "--quiet", status=status))
        self.assertEqual(inst.compose_hint(), self.expected_hint(hub, status))

    def test_the_constant_names_the_hub_file(self):
        self.assertEqual(getattr(install, "PRODUCTION_HUB_OVERLAY", None), HUB_FILE)

    def test_hub_profile_in_the_env_file_adds_the_hub_file_after_the_production_file(self):
        for profiles in ENABLED:
            with self.subTest(profiles=profiles):
                self.assert_hub_file(self.production(file_profiles=profiles), True)

    def test_hub_profile_in_the_environment_adds_the_hub_file_when_the_env_file_sets_none(self):
        for profiles in ENABLED:
            with self.subTest(profiles=profiles):
                self.assert_hub_file(self.production(environ_profiles=profiles), True)

    def test_other_profiles_never_add_the_hub_file(self):
        for profiles in NOT_ENABLED:
            for where in ("file_profiles", "environ_profiles"):
                with self.subTest(profiles=profiles, where=where):
                    self.assert_hub_file(self.production(**{where: profiles}), False)

    def test_the_env_file_value_wins_over_an_exported_one(self):
        # bin/update's own Compose commands drop an export of a key the env file sets.
        for file_profiles, environ_profiles, hub in (
                ("ml", "hub", False), ("", "hub", False), ("hub", "ml", True), ("hub", "", True)):
            with self.subTest(file_profiles=file_profiles, environ_profiles=environ_profiles):
                inst = self.production(file_profiles, environ_profiles)
                self.assert_hub_file(inst, hub)

    def test_profiles_check_plans_with_select_the_hub_file(self):
        inst = self.production(file_profiles="ml")
        inst.overlay_env({"COMPOSE_PROFILES": "ml,hub"})
        self.assert_hub_file(inst, True)
        inst = self.production(file_profiles="hub")
        inst.overlay_env({"COMPOSE_PROFILES": None})  # planned as unset: Compose sees ""
        self.assert_hub_file(inst, False)

    def test_a_registered_status_overlay_comes_after_the_hub_file(self):
        for profiles, hub in (("hub", True), ("ml", False)):
            with self.subTest(profiles=profiles):
                inst = self.production(profiles, status_overlay=self.root / STATUS_FILE)
                self.assert_hub_file(inst, hub, status=True)

    def test_ownership_files_are_the_compose_files_in_the_same_order(self):
        # Compose records every -f file in each container's labels, and ownership compares
        # them in order.
        for profiles, hub in (("hub", True), ("hubx", False), (None, False)):
            for status in (False, True):
                with self.subTest(profiles=profiles, status=status):
                    overlay = {"status_overlay": self.root / STATUS_FILE} if status else {}
                    inst = self.production(file_profiles=profiles, **overlay)
                    self.assertEqual([str(path) for path in inst._ownership_files()],
                                     [str(self.root / name) for name in self.files(hub, status)])

    def test_standalone_and_distributed_never_name_compose_files(self):
        for env_text in ("COMPOSE_PROFILES=hub\n",
                         "COMPOSE_FILE=docker-compose.distributed.yml\nCOMPOSE_PROFILES=hub\n"):
            with self.subTest(env=env_text):
                self.write(".env", env_text)
                inst = Installation(self.root, environ={"COMPOSE_PROFILES": "hub"},
                                    runner=no_commands, compose=COMPOSE)
                self.assertEqual(inst.compose_args("config"), [*COMPOSE, "config"])
                self.assertEqual(inst.compose_hint(), "docker compose")

    def test_a_missing_hub_file_is_refused_by_name_when_the_hub_is_on(self):
        (self.root / HUB_FILE).unlink()
        for where in ("file_profiles", "environ_profiles"):
            with self.subTest(where=where):
                with self.assertRaises(InstallError) as caught:
                    self.production(**{where: "hub"})
                self.assertIn(f"{HUB_FILE} is missing from {self.root}", str(caught.exception))
        # With the Hub off the file is not needed.
        self.assert_hub_file(self.production(file_profiles="ml"), False)

    def test_an_unreadable_env_file_leaves_the_hub_file_out_of_the_hint_only(self):
        inst = self.production(file_profiles="hub")
        self.assertEqual(inst.compose_hint(), self.expected_hint(True))
        inst.env_file.unlink()
        # The hint is text for a message; the commands and ownership fail closed.
        self.assertEqual(inst.compose_hint(), self.expected_hint(False))
        with self.assertRaises(EnvFileError):
            inst.compose_args("config")
        with self.assertRaises(EnvFileError):
            inst._ownership_files()

    def test_labels_from_before_the_hub_file_are_refused_with_the_fix(self):
        inst = self.labelled("hub", self.files(False))
        with self.assertRaises(InstallError) as caught:
            inst.check_ownership()
        message = str(caught.exception)
        self.assertIn("records different config_files", message)
        self.assertIn(f"it was started without {HUB_FILE}", message)
        self.assertIn(f"`{self.expected_hint(True)} up -d --no-build --wait`", message)
        self.assertIn("re-run deploy/setup.sh", message)

    def test_only_labels_missing_just_the_hub_file_get_that_fix(self):
        for profiles, recorded, owned in (
                ("hub", self.files(True), True),
                ("ml", self.files(False), True),
                ("hub", [DISTRIBUTED_FILE], False),
                ("ml", self.files(True), False),
                ("hub", [PRODUCTION_OVERLAY, DISTRIBUTED_FILE, HUB_FILE], False)):
            with self.subTest(profiles=profiles, recorded=recorded):
                inst = self.labelled(profiles, recorded)
                if owned:
                    inst.check_ownership()
                    continue
                with self.assertRaises(InstallError) as caught:
                    inst.check_ownership()
                self.assertIn("records different config_files", str(caught.exception))
                self.assertNotIn("re-run deploy/setup.sh", str(caught.exception))


class HubDocsTests(unittest.TestCase):
    """The production overlay no longer turns the Hub on; the docs tell operators what does."""

    def test_both_readmes_say_a_production_hub_operator_sets_compose_profiles_hub(self):
        update = words("deploy/update/README.md")
        self.assertIn("runs the Omega Hub only when its operator sets " + HUB_SETTING, update)
        deploy = words("deploy/README.md")
        self.assertIn("To run the Omega Hub, set " + HUB_SETTING, deploy)
        self.assertIn("To run the Hub in production, set " + HUB_SETTING, deploy)
        self.assertIn("| `COMPOSE_PROFILES` | `hub`", deploy)

    def test_env_reference_rows_say_a_production_hub_operator_sets_compose_profiles_hub(self):
        for key in HUB_ROWS:
            with self.subTest(key=key):
                self.assertIn("A production Hub operator sets " + HUB_SETTING, reference_row(key))


if __name__ == "__main__":
    unittest.main()
