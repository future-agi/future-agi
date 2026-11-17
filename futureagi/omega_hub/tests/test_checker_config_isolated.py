"""No Django startup/DB/network: run with python3 -I <this file>.

Exercise the real lazy config accessor with only django.conf stubbed. API/readiness integration
is separately covered by test_readiness_gates.py in the PostgreSQL qualification suite.
"""

import os
import runpy
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

HUB_ROOT = Path(__file__).resolve().parents[1]
ROOT = HUB_ROOT.parents[1]
TOKEN = "test-generated-token-0123456789"
PLACEHOLDER = "local-dev-only-hub-checker-token-replace-me"


class CheckerConfiguration(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        for target in (
            "socket.socket",
            "socket.create_connection",
            "socket.getaddrinfo",
        ):
            patch(
                target,
                side_effect=AssertionError("network forbidden in isolated tests"),
            ).start()
        self.settings = SimpleNamespace()
        django_conf = ModuleType("django.conf")
        django_conf.settings = self.settings
        patch.dict(
            sys.modules, {"django": ModuleType("django"), "django.conf": django_conf}
        ).start()
        self.conf = runpy.run_path(str(HUB_ROOT / "conf.py"))

    def token(self):
        return self.conf["checker_token"]()

    def test_generated_token_cannot_enable_a_disabled_standalone_checker(self):
        self.settings.OMEGA_HUB_CHECKER_TOKEN = TOKEN
        for profiles in ("", "sandbox", "ml,sandbox", "hub-extra"):
            with self.subTest(profiles=profiles):
                self.settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
                self.assertIsNone(self.token())

    def test_enabled_profile_uses_the_configured_token(self):
        self.settings.OMEGA_HUB_CHECKER_TOKEN = TOKEN
        for profiles in ("hub", "ml,hub", " sandbox, hub ", "*"):
            with self.subTest(profiles=profiles):
                self.settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
                self.assertEqual(self.token(), TOKEN)

    def test_empty_or_published_placeholder_is_never_a_real_token(self):
        self.settings.OMEGA_HUB_COMPOSE_PROFILES = "hub"
        for token in (None, "", "   ", PLACEHOLDER):
            with self.subTest(token=token):
                self.settings.OMEGA_HUB_CHECKER_TOKEN = token
                self.assertIsNone(self.token())

    def test_deployments_without_the_standalone_marker_keep_their_token(self):
        # Helm supplies its own checker. Standalone and Distributed carry the marker.
        # An unrelated Compose variable is ignored when the marker is absent.
        os.environ["COMPOSE_PROFILES"] = "sandbox"
        self.settings.OMEGA_HUB_CHECKER_TOKEN = TOKEN
        self.assertEqual(self.token(), TOKEN)

    def test_environment_marker_including_empty_is_read_lazily(self):
        os.environ["OMEGA_HUB_CHECKER_TOKEN"] = TOKEN
        os.environ["OMEGA_HUB_COMPOSE_PROFILES"] = ""
        self.assertIsNone(self.token())
        os.environ["OMEGA_HUB_COMPOSE_PROFILES"] = "hub"
        self.assertEqual(self.token(), TOKEN)
        os.environ["OMEGA_HUB_COMPOSE_PROFILES"] = "sandbox"
        self.assertIsNone(self.token())

    def test_settings_override_environment_including_explicit_disabled_profile(self):
        os.environ["OMEGA_HUB_CHECKER_TOKEN"] = TOKEN
        os.environ["OMEGA_HUB_COMPOSE_PROFILES"] = "hub"
        self.settings.OMEGA_HUB_COMPOSE_PROFILES = ""
        self.assertIsNone(self.token())

    def test_standalone_compose_wires_the_marker_and_has_no_default_secret(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        app = compose.split("  app:\n", 1)[1].split("\n  postgres:", 1)[0]
        self.assertIn("OMEGA_HUB_COMPOSE_PROFILES: ${COMPOSE_PROFILES:-}", app)
        self.assertIn("OMEGA_HUB_CHECKER_TOKEN: ${OMEGA_HUB_CHECKER_TOKEN:-}", app)
        self.assertNotIn(PLACEHOLDER, compose)


if __name__ == "__main__":
    unittest.main()
