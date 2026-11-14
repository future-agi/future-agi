"""E2E stacks render every Django service with the loopback telemetry URL.

Compose rendering only ('config' does not contact the Docker daemon); no
container is started.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOOPBACK = "http://127.0.0.1:9"
STACKS = {
    # bin/e2e: the env file plus the base and overlay compose files it passes.
    "distributed": (
        "e2e/stack/e2e.env",
        ("docker-compose.distributed.yml", "e2e/stack/docker-compose.e2e.yml"),
    ),
    "standalone": (
        "e2e/stack/standalone-e2e.env",
        ("docker-compose.yml", "e2e/stack/docker-compose.standalone-e2e.yml"),
    ),
}


# Values a developer may have exported for their own install; the E2E stacks
# must not pick them up.
EXPORTED = {
    "FUTURE_AGI_TELEMETRY_URL": "https://example.invalid",
    "FUTURE_AGI_TELEMETRY_DISABLED": "false",
}


def host_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    # Do not inherit credentials or another project's settings from the host.
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME")}
    env.update(extra or {})
    return env


def render(
    env_file: str,
    files: tuple[str, ...],
    extra_env: dict[str, str] | None = None,
    all_profiles: bool = False,
) -> dict:
    try:
        from .compose_test_tools import compose_command
    except ImportError:
        from compose_test_tools import compose_command
    env = host_env(extra_env)
    args = [*compose_command(), "--env-file", env_file]
    for name in files:
        args.extend(["-f", name])
    if all_profiles:
        args.extend(["--profile", "*"])
    result = subprocess.run(
        [*args, "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def render_with_bin_e2e(stack: str, extra_env: dict[str, str]) -> dict:
    # `bin/e2e compose config` only renders; it does not start containers.
    env = host_env({**extra_env, "E2E_STACK": stack})
    result = subprocess.run(
        [str(ROOT / "bin" / "e2e"), "compose", "--profile", "*", "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def telemetry_services(services: dict) -> dict:
    return {
        name: (service.get("environment") or {})
        for name, service in services.items()
        if "FUTURE_AGI_TELEMETRY_URL" in (service.get("environment") or {})
    }


class E2ETelemetryEnvironmentTests(unittest.TestCase):
    def assert_loopback(self, configured: dict, expected: tuple[str, ...]):
        self.assertTrue(set(expected) <= set(configured), sorted(configured))
        for name, environment in configured.items():
            self.assertEqual(environment["FUTURE_AGI_TELEMETRY_URL"], LOOPBACK, name)
            self.assertEqual(
                environment.get("FUTURE_AGI_TELEMETRY_DISABLED"), "true", name
            )

    def test_exported_telemetry_settings_do_not_change_e2e_compose_files(self):
        expected = {
            "distributed": ("backend", "worker", "worker-default", "postgres-schema-bootstrap"),
            "standalone": ("app",),
        }
        for stack, (env_file, files) in STACKS.items():
            with self.subTest(stack=stack):
                services = render(env_file, files, EXPORTED, all_profiles=True)
                self.assert_loopback(telemetry_services(services["services"]), expected[stack])

    def test_every_application_image_service_has_loopback_telemetry(self):
        # Services running an application image, including one-shot jobs that
        # do not take the shared backend environment.
        images = ("futureagi/future-agi", "futureagi/serving", "futureagi/standalone")
        for stack, (env_file, files) in STACKS.items():
            with self.subTest(stack=stack):
                services = render(env_file, files, EXPORTED, all_profiles=True)["services"]
                selected = {
                    name: (service.get("environment") or {})
                    for name, service in services.items()
                    if (service.get("image") or "").startswith(images)
                }
                self.assertTrue(selected)
                for name, environment in selected.items():
                    self.assertEqual(
                        environment.get("FUTURE_AGI_TELEMETRY_URL"), LOOPBACK, name
                    )
                    self.assertEqual(
                        environment.get("FUTURE_AGI_TELEMETRY_DISABLED"), "true", name
                    )

    def test_bin_e2e_keeps_loopback_telemetry_when_host_exports_other_values(self):
        expected = {
            "distributed": ("backend", "worker", "worker-default", "postgres-schema-bootstrap"),
            "standalone": ("app",),
        }
        for stack in STACKS:
            with self.subTest(stack=stack):
                services = render_with_bin_e2e(stack, EXPORTED)["services"]
                self.assert_loopback(telemetry_services(services), expected[stack])

    def test_every_service_with_telemetry_settings_uses_loopback(self):
        for stack, (env_file, files) in STACKS.items():
            with self.subTest(stack=stack):
                services = render(env_file, files)["services"]
                configured = {
                    name: (service.get("environment") or {})
                    for name, service in services.items()
                    if "FUTURE_AGI_TELEMETRY_URL" in (service.get("environment") or {})
                }
                self.assertTrue(configured)
                for name, environment in configured.items():
                    self.assertEqual(
                        environment["FUTURE_AGI_TELEMETRY_URL"], LOOPBACK, name
                    )
                    self.assertEqual(
                        environment.get("FUTURE_AGI_TELEMETRY_DISABLED"), "true", name
                    )

    def test_distributed_application_services_are_covered(self):
        env_file, files = STACKS["distributed"]
        services = render(env_file, files)["services"]
        # Every Django service bin/e2e starts, including the queue workers it
        # manages, and the one-shot bootstrap jobs that run before them.
        for name in (
            "backend",
            "worker",
            "worker-exact-aggregation",
            "worker-sql-workbench",
            "postgres-schema-bootstrap",
            "clickhouse-native-bootstrap",
            "clickhouse-cdc-bootstrap",
        ):
            with self.subTest(service=name):
                self.assertEqual(
                    services[name]["environment"]["FUTURE_AGI_TELEMETRY_URL"],
                    LOOPBACK,
                )

    def test_standalone_app_is_covered(self):
        env_file, files = STACKS["standalone"]
        services = render(env_file, files)["services"]
        self.assertEqual(
            services["app"]["environment"]["FUTURE_AGI_TELEMETRY_URL"], LOOPBACK
        )


if __name__ == "__main__":
    unittest.main()
