"""The production Hub file: daemon-free Compose renders; no service is started.

deploy/docker-compose.production.yml renders without any Omega Hub setting.
deploy/docker-compose.production.hub.yml, added after it when COMPOSE_PROFILES
enables the hub profile, requires every Hub setting and names a missing one.
The services that run Django see the Hub as on (OMEGA_HUB_COMPOSE_PROFILES)
only with the hub profile, and the Hub's real checker accessor
(futureagi/omega_hub/conf.py, run with django.conf stubbed: no Django start,
database or network) then returns the rendered checker token, or None without
it. Every render passes --env-file /dev/null and only PATH and HOME from this
process, plus made-up values chosen by each key's name (never real secrets).
The compose files are read only here, when the test runs.
"""

import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTED = "docker-compose.distributed.yml"
PRODUCTION = "deploy/docker-compose.production.yml"
PRODUCTION_HUB = "deploy/docker-compose.production.hub.yml"
# The settings a production Hub install supplies. The production file alone needs none of them.
HUB_KEYS = (
    "OMEGA_HUB_CHECKER_TOKEN",
    "OMEGA_HUB_APP_URL",
    "OMEGA_HUB_PUBLIC_API_URL",
    "OMEGA_HUB_CHECKER_VERSION",
    "OMEGA_HUB_RUNNER_IMAGE",
    "OMEGA_HUB_RUNNER_DOCKER_HOST",
    "OMEGA_HUB_RUNNER_TLS_DIR",
)
# A key a compose file declares required: ${KEY:?message}.
REQUIRED = re.compile(r"\$\{([A-Z_0-9]+):\?")


def required_keys(name):
    """The keys compose file `name` requires, read when the test runs."""
    return set(REQUIRED.findall((ROOT / name).read_text(encoding="utf-8")))


def made_up_value(key):
    """A test value shaped like what the key's name asks for; never a real secret."""
    slug = key.lower().replace("_", "-")
    if key.endswith(("_URL", "_API")):
        return f"https://{slug}.example.invalid"
    if key.endswith("_VERSION"):
        return "test-release"
    if key.endswith("_IMAGE"):
        return f"registry.example.invalid/{slug}@sha256:" + "0" * 64
    if key.endswith("_DOCKER_HOST"):
        return f"tcp://{slug}.example.invalid:2376"
    if key.endswith("_DIR"):
        return f"/nonexistent/{slug}"
    return f"test-{slug}"


def runs_django(service):
    """Whether a rendered service runs the Django app (it names its settings or service type)."""
    environment = service.get("environment") or {}
    return "DJANGO_SETTINGS_MODULE" in environment or "SERVICE_TYPE" in environment


def hub_entries(value):
    """The entries of an OMEGA_HUB_COMPOSE_PROFILES value that turn the Hub on (`hub`, `*`)."""
    return {"hub", "*"} & {entry.strip() for entry in str(value or "").split(",")}


def checker_token(environment):
    """What the Hub's conf.checker_token() returns in a process with this rendered environment.

    Only the two keys it reads are passed; django.conf is a stub with no settings, so the
    accessor falls back to the environment as it does in a container.
    """
    django_conf = ModuleType("django.conf")
    django_conf.settings = SimpleNamespace()
    keys = ("OMEGA_HUB_COMPOSE_PROFILES", "OMEGA_HUB_CHECKER_TOKEN")
    env = {key: str(environment[key]) for key in keys if environment.get(key) is not None}
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(
            sys.modules, {"django": ModuleType("django"), "django.conf": django_conf}):
        conf = runpy.run_path(str(ROOT / "futureagi/omega_hub/conf.py"))
        return conf["checker_token"]()


def compose_words():
    try:
        from .compose_test_tools import compose_command
    except ImportError:
        from compose_test_tools import compose_command
    return compose_command()


@unittest.skipUnless(shutil.which("docker"),
                     "Docker CLI required for daemon-free Compose rendering")
class ProductionHubOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = compose_words()

    def render(self, files, env, *args):
        """`config --quiet` (or `config *args`) of `files` with only `env`, PATH and HOME."""
        command = [*self.compose, "--env-file", os.devnull]
        for name in files:
            command += ["-f", str(ROOT / name)]
        # No exported setting, COMPOSE_FILE, COMPOSE_PROFILES or project name leaks in.
        environment = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME")}
        environment.update(env)
        return subprocess.run([*command, "config", *(args or ("--quiet",))], cwd=ROOT,
                              env=environment, capture_output=True, text=True, timeout=60,
                              check=False)

    def services(self, files, env):
        """The rendered services (`config --format json`), read only here."""
        result = self.render(files, env, "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["services"]

    def base_environment(self):
        """Every key the production file requires, except any Hub key: it must need none."""
        return {key: made_up_value(key) for key in required_keys(PRODUCTION)
                if not key.startswith("OMEGA_HUB_")}

    def hub_environment(self):
        """The base keys, each Hub key and each key the Hub file requires, and the hub profile."""
        self.assertTrue((ROOT / PRODUCTION_HUB).is_file(), f"{PRODUCTION_HUB} is missing")
        env = self.base_environment()
        for key in set(HUB_KEYS) | required_keys(PRODUCTION_HUB):
            env[key] = made_up_value(key)
        env["COMPOSE_PROFILES"] = "hub"
        return env

    def test_production_renders_without_any_hub_setting(self):
        result = self.render((DISTRIBUTED, PRODUCTION), self.base_environment())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hub_profile_with_the_hub_file_renders_with_every_hub_setting(self):
        result = self.render((DISTRIBUTED, PRODUCTION, PRODUCTION_HUB), self.hub_environment())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hub_file_refuses_each_missing_hub_setting_by_name(self):
        for key in HUB_KEYS:
            with self.subTest(key=key):
                env = self.hub_environment()
                del env[key]
                result = self.render((DISTRIBUTED, PRODUCTION, PRODUCTION_HUB), env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)

    @staticmethod
    def django_environments(services):
        """The environment of every rendered service that runs Django."""
        return {name: service.get("environment") or {} for name, service in services.items()
                if runs_django(service)}

    def test_production_base_with_a_filled_token_keeps_the_hub_off(self):
        # deploy/setup.sh writes a checker token into every new env file, Hub or not. Without
        # the hub profile the marker keeps the Hub off, so no upload waits for a checker.
        token = made_up_value("OMEGA_HUB_CHECKER_TOKEN")
        env = dict(self.base_environment(), OMEGA_HUB_CHECKER_TOKEN=token)
        django = self.django_environments(self.services((DISTRIBUTED, PRODUCTION), env))
        self.assertIn("backend", django)
        # The token does reach the backend: the marker alone turns the Hub off.
        self.assertEqual(django["backend"].get("OMEGA_HUB_CHECKER_TOKEN"), token)
        for name, environment in sorted(django.items()):
            with self.subTest(service=name):
                self.assertIn("OMEGA_HUB_COMPOSE_PROFILES", environment)
                self.assertFalse(hub_entries(environment["OMEGA_HUB_COMPOSE_PROFILES"]))
                self.assertIsNone(checker_token(environment))

    def test_production_with_the_hub_file_and_profile_turns_the_hub_on(self):
        env = self.hub_environment()  # COMPOSE_PROFILES=hub and a filled made-up token
        token = env["OMEGA_HUB_CHECKER_TOKEN"]
        files = (DISTRIBUTED, PRODUCTION, PRODUCTION_HUB)
        django = self.django_environments(self.services(files, env))
        backend = django["backend"]
        self.assertTrue(hub_entries(backend.get("OMEGA_HUB_COMPOSE_PROFILES")))
        self.assertEqual(checker_token(backend), token)
        # Every Django process that holds the token sees the Hub on with it too.
        for name, environment in sorted(django.items()):
            if environment.get("OMEGA_HUB_CHECKER_TOKEN"):
                with self.subTest(service=name):
                    self.assertEqual(checker_token(environment), token)


if __name__ == "__main__":
    unittest.main()
