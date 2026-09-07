"""Offline resource-ownership tests; these are not live integration evidence."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from run import (
    APPLICATION_RUN_TIMEOUT_SECONDS,
    LABEL,
    TRANSPORT_RUN_TIMEOUT_SECONDS,
    Run,
    compose_document,
    load_run,
    reserve_ports,
    save,
)


class HarnessSafetyTests(unittest.TestCase):
    def manifest(self):
        return {
            "run_id": "0123456789abcdef",
            "project": "pcmanaged-0123456789abcdef",
            "ports": {
                "http": 40001,
                "native": 40002,
                "postgres": 40003,
                "kafka": 40004,
                "otlp": 40005,
            },
            "password": "test-only-generated-password",
            "cluster_id": "test-cluster",
        }

    def test_services_never_reference_existing_resources(self):
        m = self.manifest()
        document = compose_document(m)
        self.assertEqual(set(document["services"]), {"clickhouse", "postgres", "kafka"})
        for service in document["services"].values():
            self.assertNotIn("container_name", service)
            self.assertNotIn("network_mode", service)
            self.assertNotIn("env_file", service)
            self.assertEqual(service["restart"], "no")
            self.assertEqual(service["labels"], {LABEL: m["run_id"]})
            self.assertTrue(
                all(port.startswith("127.0.0.1:") for port in service["ports"])
            )
            for mount in service.get("volumes", []):
                if mount.startswith("/"):
                    self.assertTrue(mount.endswith(":ro"), mount)
        for resources in (document["volumes"], document["networks"]):
            for resource in resources.values():
                self.assertEqual(resource, {"labels": {LABEL: m["run_id"]}})

    def test_port_allocation_is_distinct(self):
        ports = reserve_ports()
        self.assertEqual(len(set(ports.values())), 5)
        self.assertTrue(all(1024 < port < 65536 for port in ports.values()))

    def test_aggregate_budget_expands_only_for_application_runs(self):
        self.assertEqual(TRANSPORT_RUN_TIMEOUT_SECONDS, 900)
        self.assertEqual(APPLICATION_RUN_TIMEOUT_SECONDS, 2400)
        for extra, expected in (
            ({}, 900),
            ({"application": False}, 900),
            ({"application": True}, 2400),
        ):
            with (
                self.subTest(extra=extra),
                patch("run.time.monotonic", return_value=100),
            ):
                run = Run(Path("/tmp/isolated"), {**self.manifest(), **extra})
                self.assertEqual(run.deadline, 100 + expected)
        with patch("run.time.monotonic", return_value=100):
            run = Run(
                Path("/tmp/isolated"),
                {**self.manifest(), "application": True},
                timeout=90,
            )
        self.assertEqual(run.deadline, 190)

    def test_application_chain_budget_respects_parent_deadline_and_reserve(self):
        from application_smoke import (
            APPLICATION_CHAIN_TIMEOUT_SECONDS,
            APPLICATION_PARENT_RESERVE_SECONDS,
            _application_chain_deadline,
        )

        self.assertEqual(APPLICATION_CHAIN_TIMEOUT_SECONDS, 2100)
        self.assertEqual(APPLICATION_PARENT_RESERVE_SECONDS, 20)
        with patch("application_smoke.time.monotonic", return_value=100):
            self.assertEqual(_application_chain_deadline(2500), 2200)
            self.assertEqual(_application_chain_deadline(1200), 1180)
            self.assertEqual(_application_chain_deadline(110), 90)

    def test_application_aggregate_budget_does_not_expand_operation_caps(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("run.time.monotonic", return_value=100),
        ):
            run = Run(Path(tmp), {**self.manifest(), "application": True})
            result = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("run.subprocess.run", return_value=result) as command:
                run.command(["fixture-stage"])
                self.assertEqual(command.call_args.kwargs["timeout"], 60)
                for cap in (45, 60, 90, 100):
                    with self.subTest(cap=cap):
                        run.command(["fixture-stage"], timeout=cap)
                        self.assertEqual(command.call_args.kwargs["timeout"], cap)
                run.deadline = 105
                run.command(["fixture-stage"], timeout=100)
                self.assertEqual(command.call_args.kwargs["timeout"], 5)
                run.deadline = 100
                command.reset_mock()
                with self.assertRaisesRegex(TimeoutError, "aggregate deadline expired"):
                    run.command(["fixture-stage"], timeout=100)
                command.assert_not_called()

    def test_compose_does_not_inherit_application_environment(self):
        with patch.dict(
            "os.environ",
            {
                "COMPOSE_FILE": "/foreign/compose.yml",
                "COMPOSE_PROJECT_NAME": "production",
                "PG_PASSWORD": "secret",
                "DJANGO_SETTINGS_MODULE": "tfc.settings.settings",
            },
        ):
            run = Run(Path("/tmp/isolated"), self.manifest())
        for key in (
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "PG_PASSWORD",
            "DJANGO_SETTINGS_MODULE",
        ):
            self.assertNotIn(key, run.env)
        self.assertIn("--env-file", run.compose)

    def test_real_collector_is_opt_in_isolated_and_has_no_version_configuration(self):
        m = {
            **self.manifest(),
            "application": True,
            "candidate_topic": "test.candidates",
        }
        document = compose_document(m)
        collector = document["services"]["collector"]
        self.assertEqual(collector["profiles"], ["ingestion"])
        self.assertEqual(collector["ports"], ["127.0.0.1:40005:4318"])
        self.assertEqual(collector["user"], "65532:65532")
        self.assertTrue(collector["read_only"])
        self.assertEqual(collector["cap_drop"], ["ALL"])
        self.assertNotIn("volumes", collector)
        self.assertEqual(collector["image"], m["project"] + "-collector:local")
        self.assertEqual(collector["pull_policy"], "never")
        self.assertEqual(collector["environment"]["FI_AUTH_REDIS_ADDR"], "redis:6379")
        self.assertTrue(
            collector["environment"]["FI_PG_READ"].startswith(
                "postgres://managed_smoke_collector:"
            )
        )
        self.assertEqual(
            collector["environment"]["FI_PG_WRITE"],
            collector["environment"]["FI_PG_READ"],
        )
        self.assertEqual(document["services"]["redis"]["ports"], [])
        self.assertNotIn("volumes", document["services"]["redis"])
        for key in (
            "FI_PROPERTY_CATALOG_EPOCH",
            "FI_PROPERTY_CATALOG_PROJECTION_VERSION",
            "FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID",
            "FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST",
        ):
            self.assertNotIn(key, collector["environment"])
        self.assertNotIn("collector", compose_document(self.manifest())["services"])

    def test_cleanup_refuses_foreign_container(self):
        run = Run(Path("/tmp/isolated"), self.manifest())
        from subprocess import CompletedProcess

        results = [
            CompletedProcess([], 0, "foreign-id\n", ""),
            CompletedProcess(
                [], 0, json.dumps([{"Config": {"Labels": {LABEL: "someone-else"}}}]), ""
            ),
        ]
        with patch.object(run, "command", side_effect=results) as command:
            with self.assertRaisesRegex(RuntimeError, "ownership token"):
                run.cleanup()
        self.assertFalse(any("down" in call.args[0] for call in command.call_args_list))

    def test_load_refuses_modified_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "property-catalog-managed-0123456789abcdef"
            directory.mkdir()
            m = self.manifest()
            save(directory / "compose.json", compose_document(m))
            m["compose_sha256"] = hashlib.sha256(
                (directory / "compose.json").read_bytes()
            ).hexdigest()
            save(directory / "manifest.json", m)
            self.assertEqual(load_run(directory).manifest["project"], m["project"])
            save(directory / "compose.json", {"name": "foreign"})
            with self.assertRaisesRegex(ValueError, "changed"):
                load_run(directory)

    def test_actual_process_environments_never_supply_version_identity(self):
        from ordered_chain import environments

        m = {
            **self.manifest(),
            "database": "property_catalog_dev_smoke_test",
            "candidate_topic": "smoke.candidates",
            "ordered_topic": "smoke.ordered",
        }
        run = Run(Path("/tmp/isolated"), m)
        seq, consumer = environments(
            run, {"revision_fence_file": "/tmp/isolated/runtime/revision-fence-v2.json"}
        )
        for env in (seq, consumer):
            self.assertEqual(
                env["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"],
                "/tmp/isolated/runtime/revision-fence-v2.json",
            )
            for name in (
                "FI_PROPERTY_CATALOG_EPOCH",
                "FI_PROPERTY_CATALOG_PROJECTION_VERSION",
                "FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID",
            ):
                self.assertNotIn(name, env)
        self.assertEqual(
            consumer["FI_PROPERTY_CATALOG_CH_USERNAME"], "property_catalog_oss_consumer"
        )
        self.assertEqual(
            consumer["FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"],
            "property_catalog_oss_ledger",
        )
        self.assertGreaterEqual(
            int(consumer["FI_PROPERTY_CATALOG_CHECKPOINT_MAX_STREAMS"]), 4 * 10
        )

    def test_identity_admission_delegates_owned_routes_to_normal_initializer(self):
        from identity_smoke import _prepare_write_admission

        m = {
            **self.manifest(),
            "database": "property_catalog_dev_smoke_test",
            "candidate_topic": "smoke.candidates",
            "ordered_topic": "smoke.ordered",
        }
        settings = SimpleNamespace(
            PROPERTY_CATALOG_DEV_WRITE_CH_HOST="127.0.0.1",
            PROPERTY_CATALOG_DEV_WRITE_CH_PORT=m["ports"]["native"],
            PROPERTY_CATALOG_DEV_WRITE_CH_USER="property_catalog_oss_control",
            PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD="oss-catalog-control-local-only",
        )
        original = dict(vars(settings))
        client_factory, admission = object(), object()
        initializer = Mock(return_value=admission)
        module = "tracer.services.clickhouse.v2.property_catalog.oss_write_startup"
        # Only the initializer boundary is mocked. This remains a stdlib-only
        # harness test; real producer/permission checks have their own suite.
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "identity-runtime"
            runtime.mkdir(mode=0o700)
            fence = str(runtime / "revision-fence-v2.json")
            with (
                patch.dict(
                    "sys.modules",
                    {module: SimpleNamespace(prepare_oss_write_admission=initializer)},
                ),
                patch.dict(
                    "os.environ",
                    {
                        "FI_PROPERTY_CATALOG_LEDGER_CH_URL": "http://foreign.invalid:8123",
                        "FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE": "foreign",
                        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "foreign_writer",
                        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "foreign-secret",
                    },
                ),
            ):
                self.assertIs(
                    _prepare_write_admission(
                        settings, fence, m, client_factory=client_factory
                    ),
                    admission,
                )
                initializer.assert_called_once()
                configured = initializer.call_args.args[0]
                self.assertEqual(
                    vars(configured),
                    {
                        **original,
                        "ENV_TYPE": "development",
                        "CLOUD_DEPLOYMENT": "",
                        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": fence,
                        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": m["database"],
                        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": m["database"],
                        "PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC": m["candidate_topic"],
                        "PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC": m["ordered_topic"],
                    },
                )
                self.assertEqual(
                    initializer.call_args.kwargs,
                    {
                        "client_factory": client_factory,
                        "environ": {
                            "FI_PROPERTY_CATALOG_LEDGER_CH_URL": "http://127.0.0.1:40001",
                            "FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE": m["database"],
                            "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "property_catalog_oss_ledger",
                            "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "oss-catalog-ledger-local-only",
                        },
                    },
                )
                initializer.reset_mock()
                initializer.side_effect = RuntimeError("admission proof rejected")
                with self.assertRaisesRegex(RuntimeError, "admission proof rejected"):
                    _prepare_write_admission(
                        settings, fence, m, client_factory=client_factory
                    )
                initializer.assert_called_once()
            # The fixture wrapper does not create a replacement descriptor or
            # weaken directory permissions when admission fails.
            self.assertEqual(list(runtime.iterdir()), [])
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
        self.assertEqual(vars(settings), original)

    def test_application_uses_owned_readonly_database_and_no_version_settings(self):
        from application_smoke import backend_environment

        m = {
            **self.manifest(),
            "database": "property_catalog_dev_smoke_test",
            "candidate_topic": "smoke.candidates",
            "ordered_topic": "smoke.ordered",
        }
        env = backend_environment(Run(Path("/tmp/isolated"), m), reader=True)
        self.assertEqual(env["PG_USER"], "property_catalog_oss_reader")
        self.assertEqual(env["PGBOUNCER_HOST"], "127.0.0.1")
        self.assertEqual(env["PGBOUNCER_PORT"], "40003")
        for name in (
            "PROPERTY_CATALOG_DEV_CATALOG_EPOCH",
            "PROPERTY_CATALOG_DEV_PROJECTION_VERSION",
            "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID",
        ):
            self.assertNotIn(name, env)


if __name__ == "__main__":
    unittest.main()
