"""Offline dependency preflight contracts; no Django setup or database access."""

import builtins
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from application_base import REQUIRED_IMPORTS

SPEC = importlib.util.spec_from_file_location(
    "replicated_application_dependency_runner",
    Path(__file__).resolve().parent.parent
    / "managed_smoke"
    / "linux_application_runner.py",
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class DependencyPreflightTests(unittest.TestCase):
    def test_restart_requires_fresh_health_without_ignoring_new_failure(self):
        old = json.dumps(
            {"phase": "idle", "detail": {"failed_count": 1}, "stopped": True}
        ).encode()
        self.assertFalse(runner.health_observation_ready(old, old))
        with self.assertRaises(RuntimeError):
            runner.health_observation_ready(old)
        new_failure = json.dumps({"phase": "retrying", "detail": {}}).encode()
        with self.assertRaises(RuntimeError):
            runner.health_observation_ready(new_failure, old)
        fresh = json.dumps(
            {"phase": "idle", "ready": True, "detail": {"failed_count": 0}}
        ).encode()
        self.assertTrue(runner.health_observation_ready(fresh, old))
        self.assertFalse(runner.health_observation_ready(fresh, fresh))

    def test_ordered_probe_uses_only_current_workspace_source_window(self):
        fixture = {
            "organization_id": "org",
            "workspace_id": "workspace",
            "project_id": "project",
        }
        fence = {
            **fixture,
            "project_ids": ["project"],
            "status": "building",
            "span_since_us": 10000,
            "span_until_us": 20000,
        }
        with patch.object(
            Path, "read_bytes", return_value=json.dumps({"fences": [fence]}).encode()
        ):
            self.assertEqual(
                runner.ordered_observation(Path("/unused"), fixture), 15000000
            )
        for changes in (
            {"workspace_id": "foreign"},
            {"status": "active"},
            {"project_ids": []},
            {"span_until_us": 10000},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(
                    Path,
                    "read_bytes",
                    return_value=json.dumps(
                        {"fences": [{**fence, **changes}]}
                    ).encode(),
                ),
                self.assertRaises(RuntimeError),
            ):
                runner.ordered_observation(Path("/unused"), fixture)

    def test_orchestrator_configures_settings_before_catalog_import(self):
        original_import = builtins.__import__

        def import_module(name, *args, **kwargs):
            if name.endswith("property_catalog.installation_identity"):
                self.assertEqual(
                    runner.os.environ["DJANGO_SETTINGS_MODULE"], "application_settings"
                )
                self.assertEqual(runner.os.environ["ENV_TYPE"], "production")
                raise RuntimeError("reached configured catalog import")
            return original_import(name, *args, **kwargs)

        with (
            patch.dict(runner.os.environ, {}, clear=True),
            patch.object(
                runner,
                "python_environment",
                return_value={
                    "DJANGO_SETTINGS_MODULE": "application_settings",
                    "ENV_TYPE": "production",
                },
            ),
            patch("builtins.__import__", side_effect=import_module),
        ):
            with self.assertRaisesRegex(RuntimeError, "configured catalog import"):
                runner.execute(Path("/unused"), {}, {})

    def test_registry_and_installed_dependency_contracts_agree(self):
        self.assertEqual(set(REQUIRED_IMPORTS), set(runner.REQUIRED_IMPORTS))
        self.assertIn("psycopg", REQUIRED_IMPORTS)

    def test_imports_psycopg3_and_records_its_distribution_version(self):
        versions = {
            "Django": "5.1.8",
            "djangorestframework": "3.16.0",
            "clickhouse-driver": "0.2.9",
            "psycopg": "3.2.9",
            "requests": "2.32.4",
        }
        with (
            patch.object(runner.importlib, "import_module") as imported,
            patch.object(
                runner.importlib.metadata, "version", side_effect=versions.__getitem__
            ) as version,
        ):
            evidence = runner.dependency_evidence()

        imported.assert_any_call("psycopg")
        version.assert_any_call("psycopg")
        self.assertEqual(evidence["packages"]["psycopg"], "3.2.9")
        self.assertNotIn("psycopg2", evidence["packages"])
        self.assertEqual(evidence["python"], runner.sys.version.split()[0])

    def test_missing_psycopg_fails_without_install_or_database_access(self):
        def import_module(name):
            if name == "psycopg":
                raise ModuleNotFoundError("psycopg is not installed")
            return object()

        with (
            patch.object(runner.importlib, "import_module", side_effect=import_module),
            patch.object(runner.importlib.metadata, "version", return_value="unused"),
            patch.object(
                runner.subprocess, "run", side_effect=AssertionError("no install")
            ) as install,
            patch.object(
                runner, "client", side_effect=AssertionError("no database")
            ) as database,
            patch.object(
                runner.socket.socket,
                "connect",
                side_effect=AssertionError("no network"),
            ) as connect,
        ):
            with self.assertRaisesRegex(ModuleNotFoundError, "psycopg"):
                runner.dependency_evidence()
            install.assert_not_called()
            database.assert_not_called()
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
