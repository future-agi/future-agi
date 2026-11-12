"""Every process that hashes or verifies an API secret resolves the same pepper.

READINESS L03: Django stores an API secret as HMAC-SHA256 under a pepper
(futureagi/accounts/api_secrets.py) and fi-collector verifies it under the same
one (fi-collector/pkg/auth/secrethash.go). Both resolve it by one rule: the
configured FI_API_SECRET_PEPPER; else, in a local or test ENV_TYPE, one derived
from SECRET_KEY; else they refuse to start. In a stack whose collector resolves
another pepper than its backend, every SDK export is refused; in one whose
collector resolves none, the collector restarts forever.

Compose rendering only (`docker compose config`); no services, no daemon.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

if __package__:
    from .test_observed_catalog_compose import ROOT, compose
    from .test_observed_catalog_startup import DEV_OVERLAY_ENV, PRODUCTION_ENV
else:
    from test_observed_catalog_compose import ROOT, compose
    from test_observed_catalog_startup import DEV_OVERLAY_ENV, PRODUCTION_ENV

SETTINGS = ROOT / "futureagi/tfc/settings/settings.py"
DJANGO_DEFAULT_SECRET_KEY = re.search(
    r'^_DEFAULT_SECRET_KEY = "([^"]+)"$', SETTINGS.read_text(encoding="utf-8"), re.M
).group(1)
PUBLISHED_PEPPER = "local-dev-only-api-secret-pepper-replace-me"
MIN_PEPPER_LENGTH = 32
OPERATOR_PEPPER = "operator-pepper-from-the-secret-store-0123456789"


def django_pepper(env: dict) -> tuple | None:
    """What tfc/settings/settings.py and accounts/api_secrets.py resolve, or None."""
    configured = (env.get("FI_API_SECRET_PEPPER") or "").strip()
    local = str(env.get("ENV_TYPE", "local")).lower() in ("local", "test")
    if not local and configured in ("", PUBLISHED_PEPPER):
        return None  # settings.py exits at start
    if configured:
        return None if len(configured) < MIN_PEPPER_LENGTH else ("configured", configured)
    return ("derived", env.get("SECRET_KEY") or DJANGO_DEFAULT_SECRET_KEY)


def collector_pepper(env: dict) -> tuple | None:
    """What ResolveSecretPeppers resolves for fi-collector, or None (it exits)."""
    configured = (env.get("FI_API_SECRET_PEPPER") or "").strip()
    if configured:
        return None if len(configured) < MIN_PEPPER_LENGTH else ("configured", configured)
    local = str(env.get("ENV_TYPE", "")).strip().lower() in ("", "local", "test")
    secret = env.get("SECRET_KEY") or ""
    return ("derived", secret) if local and secret else None


def runs_django(service: dict) -> bool:
    env = service.get("environment") or {}
    return "DJANGO_SETTINGS_MODULE" in env or "SERVICE_TYPE" in env


def runs_the_collector(service: dict) -> bool:
    entrypoint = service.get("entrypoint") or []
    if any("fi-property-catalog-consumer" in part for part in entrypoint):
        return False
    image = service.get("image") or ""
    context = str((service.get("build") or {}).get("context") or "")
    return image.startswith("futureagi/fi-collector") or context.endswith("fi-collector")


def render_in(project_dir: str, *files: str) -> dict:
    """Render with ``project_dir`` as the project: its .env feeds env_file and ${VAR}."""
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME")}
    args = ["docker", "compose", "--project-directory", project_dir]
    for name in files:
        args.extend(["-f", str(ROOT / name)])
    result = subprocess.run(
        [*args, "config", "--format", "json"],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)["services"]


class ApiSecretPepperComposeTests(unittest.TestCase):
    def assert_one_pepper(self, services: dict, expected: tuple | None = None) -> None:
        collectors = {
            name: collector_pepper(svc.get("environment") or {})
            for name, svc in services.items()
            if runs_the_collector(svc)
        }
        backends = {
            name: django_pepper(svc.get("environment") or {})
            for name, svc in services.items()
            if runs_django(svc)
        }
        self.assertTrue(collectors, "no fi-collector in this stack")
        self.assertTrue(backends, "no Django process in this stack")
        resolved = {**collectors, **backends}
        self.assertNotIn(None, resolved.values(), resolved)
        self.assertEqual(len(set(resolved.values())), 1, resolved)
        if expected is not None:
            self.assertEqual(next(iter(resolved.values())), expected)

    def test_standalone_app_hashes_and_verifies_under_one_pepper(self):
        # One container runs Django and the collector (deploy/standalone).
        for overrides, expected in (
            ({}, ("configured", PUBLISHED_PEPPER)),
            ({"FI_API_SECRET_PEPPER": OPERATOR_PEPPER}, ("configured", OPERATOR_PEPPER)),
        ):
            with self.subTest(overrides=overrides):
                env = compose(base_file="docker-compose.yml", **overrides)["services"][
                    "app"
                ]["environment"]
                self.assertEqual(django_pepper(env), expected)
                self.assertEqual(collector_pepper(env), expected)

    def test_distributed_stacks_share_one_pepper(self):
        variants = {
            "root": ((), {}),
            "dev": (("docker-compose.distributed.dev.yml",), dict(DEV_OVERLAY_ENV)),
            "e2e": (("e2e/stack/docker-compose.e2e.yml",), {}),
            "production": (("deploy/docker-compose.production.yml",), dict(PRODUCTION_ENV)),
        }
        for name, (overlays, env) in variants.items():
            for pepper in (None, OPERATOR_PEPPER):
                if name == "production" and pepper is None:
                    continue  # the production overlay requires one: test below
                with self.subTest(stack=name, pepper=pepper):
                    overrides = {**env, "COMPOSE_PROFILES": "full"}
                    if pepper:
                        overrides["FI_API_SECRET_PEPPER"] = pepper
                    services = compose(*overlays, **overrides)["services"]
                    want = PRODUCTION_ENV["FI_API_SECRET_PEPPER"] if name == "production" else PUBLISHED_PEPPER
                    self.assert_one_pepper(services, ("configured", pepper or want))

    def test_production_refuses_to_render_without_a_pepper(self):
        for value in ("", None):
            env = {k: v for k, v in PRODUCTION_ENV.items() if k != "FI_API_SECRET_PEPPER"}
            if value is not None:
                env["FI_API_SECRET_PEPPER"] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                AssertionError, "FI_API_SECRET_PEPPER"
            ):
                compose("deploy/docker-compose.production.yml", **env)

    def test_backend_source_compose_collector_follows_the_backend_env_file(self):
        # futureagi/docker-compose.yml (+ the harness worker): the backend and the
        # workers read futureagi/.env; the collector must resolve what they do.
        files = ("futureagi/docker-compose.yml", "futureagi/docker-compose.harness.yml")
        for dotenv, expected in (
            ("", ("derived", DJANGO_DEFAULT_SECRET_KEY)),
            ("ENV_TYPE=local\nSECRET_KEY=a-developer-secret-key\n", ("derived", "a-developer-secret-key")),
            (f"SECRET_KEY=other\nFI_API_SECRET_PEPPER={OPERATOR_PEPPER}\n", ("configured", OPERATOR_PEPPER)),
            (f"ENV_TYPE=staging\nFI_API_SECRET_PEPPER={OPERATOR_PEPPER}\n", ("configured", OPERATOR_PEPPER)),
        ):
            with self.subTest(dotenv=dotenv), tempfile.TemporaryDirectory() as project:
                Path(project, ".env").write_text(dotenv, encoding="utf-8")
                self.assert_one_pepper(render_in(project, *files), expected)

    def test_collector_rig_without_postgres_still_starts(self):
        with tempfile.TemporaryDirectory() as project:
            services = render_in(project, "fi-collector/docker-compose.standalone.yml")
        self.assertIsNotNone(collector_pepper(services["fi-collector"]["environment"]))


if __name__ == "__main__":
    unittest.main()
