"""Hermetic runtime-selection regression: execute only extracted shell conditions.

Run with: python3 -I deploy/tests/test_entrypoint_runtime.py
No application imports, service commands, environment files, or network access.
"""

import re
import subprocess
import unittest
from pathlib import Path


ENTRYPOINT = Path(__file__).resolve().parents[2] / "futureagi" / "entrypoint.sh"
BRANCHES = (
    "mutation-free Django validation",
    "mutable Django validation",
    "backend gRPC",
    "backend HTTP",
    "Celery worker",
    "Celery beat",
    "Flower",
    "standalone gRPC",
    "Temporal worker",
)
PRODUCTION_NAMES = ("prod", "production", "staging", "PROD", "PRODUCTION", "STAGING")
DEVELOPMENT_NAMES = ("development", "local", "test", "", None)


def selection(env_type, *, cloud_deployment="", inherited_runtime=""):
    source = ENTRYPOINT.read_text()
    # Like the mutation-guard tests, extract a bounded, side-effect-free section.
    default = re.search(r"^ENV_TYPE=.*$", source, re.MULTILINE).group()
    start = source.index("CLOUD_STARTUP=false")
    end = source.index("STARTUP_DB_MUTATION_MODE=", start)
    initialization = source[start:end]
    conditions = re.findall(
        r'^\s*(if \[ "\$(?:ENV_TYPE|PRODUCTION_RUNTIME)"[^\n]+; then)$',
        source,
        re.MULTILINE,
    )
    if len(conditions) != len(BRANCHES):
        raise AssertionError(
            f"Expected {len(BRANCHES)} runtime branches, got {len(conditions)}"
        )

    # Replace branch bodies with labels: never source or execute service startup.
    probes = [
        f"{condition}\nprintf 'production\\n'\nelse\nprintf 'development\\n'\nfi"
        for condition in conditions
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "CLOUD_DEPLOYMENT": cloud_deployment,
        "PRODUCTION_RUNTIME": inherited_runtime,
    }
    if env_type is not None:
        env["ENV_TYPE"] = env_type
    completed = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc"],
        input=(
            f"set -e\n{default}\n{initialization}\n"
            + "\n".join(probes)
            + '\nprintf "%s\\n" "$CLOUD_STARTUP"\n'
        ),
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=True,
    )
    rows = completed.stdout.splitlines()
    if len(rows) != len(BRANCHES) + 1:
        raise AssertionError(f"Unexpected selection output: {rows!r}")
    return dict(zip(BRANCHES, rows[:-1])), rows[-1]


class EntrypointRuntime(unittest.TestCase):
    def assert_runtime(self, env_type, expected, **kwargs):
        branches, _ = selection(env_type, **kwargs)
        for branch, actual in branches.items():
            with self.subTest(env_type=env_type, branch=branch, **kwargs):
                self.assertEqual(actual, expected)

    def test_production_aliases_select_production(self):
        for env_type in PRODUCTION_NAMES:
            self.assert_runtime(env_type, "production")

    def test_mixed_case_aliases_select_production(self):
        for env_type in ("Prod", "Production", "Staging", "pRoDuCtIoN"):
            self.assert_runtime(env_type, "production")

    def test_development_controls_select_development(self):
        for env_type in DEVELOPMENT_NAMES:
            self.assert_runtime(env_type, "development")

    def test_cloud_location_does_not_select_production_runtime(self):
        for cloud in ("US", "EU", "DEV", "us", "eu", "dev"):
            self.assert_runtime("development", "development", cloud_deployment=cloud)
            _, cloud_startup = selection("development", cloud_deployment=cloud)
            self.assertEqual(cloud_startup, "true")

    def test_runtime_flag_is_derived_not_an_environment_override(self):
        self.assert_runtime("development", "development", inherited_runtime="true")
        self.assert_runtime("production", "production", inherited_runtime="false")

    def test_cloud_startup_classification_is_unchanged(self):
        for env_type in PRODUCTION_NAMES + DEVELOPMENT_NAMES + ("Production",):
            with self.subTest(env_type=env_type):
                _, cloud_startup = selection(env_type)
                self.assertEqual(
                    cloud_startup, "true" if env_type in PRODUCTION_NAMES else "false"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
