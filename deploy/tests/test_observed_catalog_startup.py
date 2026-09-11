"""Startup ordering contracts; only render Compose, never start services.

Successful rendering proves neither database compatibility nor CDC readiness.
The installer tests own bounded waits and read-only checks inside the jobs.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import unittest
from graphlib import CycleError, TopologicalSorter

if __package__:
    from .test_observed_catalog_compose import ROOT, compose
else:
    from test_observed_catalog_compose import ROOT, compose


OVERLAYS = {
    "root": (),
    "dev": ("docker-compose.dev.yml",),
    "e2e": ("e2e/stack/docker-compose.e2e.yml",),
    "production": ("deploy/docker-compose.production.yml",),
}
# Synthetic values for the existing production overlay's required inputs.
PRODUCTION_ENV = {
    "FI_COLLECTOR_VERSION": "startup-contract-collector",
    "FUTURE_AGI_VERSION": "startup-contract-backend",
    "FRONTEND_VERSION": "startup-contract-frontend",
    "AGENTCC_GATEWAY_VERSION": "startup-contract-gateway",
    "SERVING_VERSION": "startup-contract-serving",
    "CODE_EXECUTOR_VERSION": "startup-contract-executor",
    "SIMULATION_RUNNER_VERSION": "startup-contract-simulation",
    "SECRET_KEY": "startup-contract-secret",
    "AGENTCC_INTERNAL_API_KEY": "startup-contract-internal-key",
    "AGENTCC_ADMIN_TOKEN": "startup-contract-admin-token",
    "PG_PASSWORD": "startup-contract-pg-password",
    "MINIO_ROOT_PASSWORD": "startup-contract-minio-password",
    "RABBITMQ_USER": "startup-contract-user",
    "RABBITMQ_PASSWORD": "startup-contract-rabbit-password",
    "PROPERTY_CATALOG_API_PASSWORD": "startup-contract-catalog-reader",
    "PROPERTY_CATALOG_CONSUMER_PASSWORD": "startup-contract-catalog-writer",
    "FRONTEND_URL": "https://app.example.com",
    "VITE_HOST_API": "https://api.example.com",
}
PG_BOOTSTRAP = "postgres-schema-bootstrap"
INDEX_BOOTSTRAP = "property-catalog-clickhouse-bootstrap"
INSTALLER = "tracer.services.clickhouse.oss_cdc_install"
PEERDB_SETUP = "tracer.services.clickhouse.oss_cdc_setup"
TEMPORAL_SETUP = "tracer.services.clickhouse.oss_peerdb_temporal"
RUNTIMES = (
    "backend",
    "worker",
    "worker-default",
    "worker-tasks-s",
    "worker-tasks-l",
    "worker-tasks-xl",
    "worker-exact-aggregation",
    "worker-trace-ingestion",
    "worker-agent-compass",
    "worker-simulation-runner",
)
PEERDB_CORE = (
    "peerdb-catalog",
    "peerdb-temporal",
    "peerdb-minio",
    "peerdb-flow-api",
    "peerdb-flow-worker",
    "peerdb-flow-snapshot-worker",
    "peerdb-server",
    "peerdb-temporal-init",
    "peerdb-init",
)
OPERATOR_OVERRIDES = {
    "FAST_STARTUP": "true",
    "NO_STARTUP_DB_MUTATIONS": "false",
    "FI_SKIP_CH25_MIGRATION": "0",
    "SERVICE_TYPE": "backend",
    "REGISTER_TEMPORAL_SCHEDULES": "false",
    "CH_DATABASE": "analytics_custom",
    "CH25_DATABASE": "separate_native_spans",
    "FI_CH_DATABASE": "separate_collector_spans",
    "CH25_DROP_LEGACY_CDC_CHAIN": "false",
    "ENV_TYPE": "local",
}


def render(variant, *, full=False, overrides=None):
    env = dict(PRODUCTION_ENV) if variant == "production" else {}
    env.update(overrides or {})
    if full:
        env["COMPOSE_PROFILES"] = "full"
    return compose(*OVERLAYS[variant], **env)["services"]


def argv(service):
    def tokens(value):
        return shlex.split(value) if isinstance(value, str) else list(value or [])

    return tokens(service.get("entrypoint")) + tokens(service.get("command"))


class StartupComposeContractTests(unittest.TestCase):
    def phase_job(self, services, phase):
        # Select by the promised CLI contract, without imposing new job names.
        matches = []
        for name, service in services.items():
            command = argv(service)
            if INSTALLER in command and any(
                command[index : index + 2] == ["--phase", phase]
                for index in range(len(command))
            ):
                matches.append(name)
        self.assertEqual(len(matches), 1, f"expected one {phase} installer: {matches}")
        return matches[0]

    def assert_gate(self, services, service, dependency, condition):
        self.assertIn(service, services.keys())
        self.assertIn(dependency, services.keys())
        gate = services[service].get("depends_on", {}).get(dependency, {})
        self.assertEqual(gate.get("condition"), condition, f"{service} -> {dependency}")
        self.assertTrue(
            gate.get("required", True), f"optional gate: {service} -> {dependency}"
        )

    def assert_python_job(self, service, prefix):
        self.assertIn(
            "entrypoint", service.keys(), "must bypass the image's Django startup"
        )
        self.assertIsNotNone(service["entrypoint"])
        self.assertEqual(argv(service)[: len(prefix)], prefix)

    def test_default_peerdb_graph_is_complete_and_all_profiles_are_acyclic(self):
        for variant in OVERLAYS:
            for full in (False, True):
                with self.subTest(variant=variant, full=full):
                    services = render(variant, full=full)
                    self.assertTrue(
                        set(PEERDB_CORE) <= services.keys(), "core PeerDB missing"
                    )
                    for name in PEERDB_CORE:
                        self.assertFalse(services[name].get("profiles"), name)
                    graph = {
                        name: set(service.get("depends_on", {}))
                        for name, service in services.items()
                    }
                    for name, dependencies in graph.items():
                        self.assertTrue(dependencies <= services.keys(), name)
                    try:
                        tuple(TopologicalSorter(graph).static_order())
                    except CycleError as error:
                        self.fail(f"startup dependency cycle: {error}")
                    if variant == "dev":
                        self.assertIn("peerdb-ui", services.keys())
                    elif full:
                        self.assertTrue(services["peerdb-ui"].get("profiles"))
                    else:
                        self.assertNotIn("peerdb-ui", services.keys())

    def test_root_and_e2e_postgres_job_owns_startup_mutations(self):
        for variant in ("root", "e2e"):
            for overrides in (
                {},
                {**OPERATOR_OVERRIDES, "NO_STARTUP_DB_MUTATIONS": "true"},
            ):
                with self.subTest(variant=variant, overrides=bool(overrides)):
                    services = render(variant, overrides=overrides)
                    self.assertIn(PG_BOOTSTRAP, services.keys())
                    bootstrap = services[PG_BOOTSTRAP]
                    self.assertEqual(bootstrap["image"], services["backend"]["image"])
                    for key, value in {
                        "SERVICE_TYPE": "bootstrap",
                        "FAST_STARTUP": "false",
                        "NO_STARTUP_DB_MUTATIONS": "false",
                        "FI_SKIP_CH25_MIGRATION": "1",
                        "REGISTER_TEMPORAL_SCHEDULES": "true",
                    }.items():
                        self.assertEqual(bootstrap["environment"].get(key), value, key)
                    self.assert_gate(
                        services, PG_BOOTSTRAP, "postgres", "service_healthy"
                    )
                    self.assert_gate(
                        services, PG_BOOTSTRAP, "temporal", "service_healthy"
                    )

    def test_cli_jobs_bypass_django_and_apply_only_in_root_and_e2e(self):
        for variant in OVERLAYS:
            with self.subTest(variant=variant):
                services = render(variant)
                jobs = [
                    ("peerdb-init", PEERDB_SETUP),
                    ("peerdb-temporal-init", TEMPORAL_SETUP),
                ]
                jobs.extend(
                    (self.phase_job(services, phase), INSTALLER)
                    for phase in ("native", "cdc")
                )
                for name, module in jobs:
                    job = services[name]
                    self.assert_python_job(job, ["python", "-m", module])
                    self.assertEqual(job["image"], services["backend"]["image"])
                    self.assertEqual("--apply" in argv(job), variant in ("root", "e2e"))
                cdc_command = argv(services[self.phase_job(services, "cdc")])
                self.assertIn("--wait-for-mirrors", cdc_command)
                self.assertIn("--timeout", cdc_command)
                timeout_index = cdc_command.index("--timeout") + 1
                self.assertLess(timeout_index, len(cdc_command))
                self.assertGreater(int(cdc_command[timeout_index]), 0)

    def test_native_peerdb_and_cdc_dependency_conditions(self):
        for variant in OVERLAYS:
            with self.subTest(variant=variant):
                services = render(variant)
                native = self.phase_job(services, "native")
                cdc = self.phase_job(services, "cdc")
                success = "service_completed_successfully"
                edges = [
                    (native, PG_BOOTSTRAP, success),
                    (native, "clickhouse", "service_healthy"),
                    ("peerdb-init", PG_BOOTSTRAP, success),
                    ("peerdb-init", native, success),
                    (cdc, "peerdb-init", success),
                    (cdc, native, success),
                    ("peerdb-init", "peerdb-temporal-init", success),
                    ("peerdb-temporal-init", "peerdb-temporal", "service_healthy"),
                ]
                for worker in ("peerdb-flow-worker", "peerdb-flow-snapshot-worker"):
                    edges.extend(
                        (job, worker, "service_started") for job in ("peerdb-init", cdc)
                    )
                edges.extend(
                    (flow, "peerdb-minio", "service_healthy")
                    for flow in (
                        "peerdb-flow-api",
                        "peerdb-flow-worker",
                        "peerdb-flow-snapshot-worker",
                    )
                )
                for service, dependency, condition in edges:
                    self.assert_gate(services, service, dependency, condition)

    def test_schema_and_mirror_jobs_receive_the_actual_collector_target(self):
        cases = (
            ({}, "default"),
            ({"CH_DATABASE": "analytics_custom"}, "analytics_custom"),
            (
                {"CH_DATABASE": "analytics_custom", "CH25_DATABASE": "native_custom"},
                "native_custom",
            ),
            (
                {
                    "CH_DATABASE": "analytics_custom",
                    "FI_CH_DATABASE": "separate_collector_spans",
                    "FI_CH_URL": "http://ignored-operator-target:8123",
                },
                "separate_collector_spans",
            ),
        )
        for variant in OVERLAYS:
            for overrides, database in cases:
                with self.subTest(variant=variant, overrides=overrides):
                    services = render(variant, overrides=overrides)
                    collector = services["fi-collector"]["environment"]
                    self.assertEqual(collector["FI_CH_DATABASE"], database)
                    jobs = ["peerdb-init"]
                    jobs.extend(
                        self.phase_job(services, phase) for phase in ("native", "cdc")
                    )
                    # Forward the rendered writer target even when it is split;
                    # the shared CLI validator owns rejecting it before writes.
                    for job in jobs:
                        for key in ("FI_CH_DATABASE", "FI_CH_URL"):
                            self.assertEqual(
                                services[job]["environment"].get(key),
                                collector[key],
                                f"{job}: {key}",
                            )

    def test_peerdb_initializer_has_no_sql_endpoint_or_server_dependency(self):
        for variant in OVERLAYS:
            with self.subTest(variant=variant):
                services = render(
                    variant,
                    overrides={"PEERDB_HOST": "unused-sql-host", "PEERDB_PORT": "9999"},
                )
                job = services["peerdb-init"]
                for key in ("PEERDB_HOST", "PEERDB_PORT"):
                    self.assertTrue(key not in job["environment"], f"obsolete {key}")
                self.assertNotIn("peerdb-server", job["depends_on"])
                self.assert_gate(
                    services, "peerdb-init", "peerdb-flow-api", "service_healthy"
                )
                for worker in ("peerdb-flow-worker", "peerdb-flow-snapshot-worker"):
                    self.assert_gate(services, "peerdb-init", worker, "service_started")

    def test_flow_api_health_uses_bounded_existing_image_http_probe(self):
        for variant in OVERLAYS:
            with self.subTest(variant=variant):
                health = render(variant)["peerdb-flow-api"].get("healthcheck", {})
                self.assertEqual(
                    health.get("test"),
                    [
                        "CMD",
                        "wget",
                        "-q",
                        "-T",
                        "3",
                        "-Y",
                        "off",
                        "-O",
                        "/dev/null",
                        "http://127.0.0.1:8113/v1/version",
                    ],
                )
                self.assertFalse(health.get("disable", False))
                self.assertEqual(health.get("interval"), "5s")
                self.assertEqual(health.get("timeout"), "5s")
                self.assertEqual(health.get("retries"), 30)
                self.assertEqual(health.get("start_period"), "10s")

    def test_bootstrap_credentials_follow_nondefault_application_users(self):
        for variant in OVERLAYS:
            with self.subTest(variant=variant):
                services = render(
                    variant,
                    overrides={
                        "PG_USER": "custom_source_user",
                        "CH_USERNAME": "custom_ch_user",
                    },
                )
                backend = services["backend"]["environment"]
                jobs = [PG_BOOTSTRAP, "peerdb-init", "peerdb-temporal-init"]
                jobs.extend(
                    self.phase_job(services, phase) for phase in ("native", "cdc")
                )
                for job in jobs:
                    env = services[job]["environment"]
                    for primary, fallback in (
                        ("SRC_PG_USER", "PG_USER"),
                        ("SRC_PG_PASSWORD", "PG_PASSWORD"),
                        ("DST_CH_USER", "CH_USERNAME"),
                        ("DST_CH_PASSWORD", "CH_PASSWORD"),
                    ):
                        self.assertTrue(
                            env.get(primary, env.get(fallback)) == backend[fallback],
                            f"{job}: {primary}/{fallback} credential mismatch",
                        )

    def test_every_runtime_gates_cdc_and_index_bootstrap(self):
        for variant in OVERLAYS:
            with self.subTest(variant=variant):
                services = render(variant, full=True)
                cdc = self.phase_job(services, "cdc")
                for runtime in RUNTIMES:
                    for dependency in (cdc, INDEX_BOOTSTRAP):
                        self.assert_gate(
                            services,
                            runtime,
                            dependency,
                            "service_completed_successfully",
                        )

    def test_collector_gates_native_and_postgres_but_consumer_only_index_and_kafka(
        self,
    ):
        for variant in OVERLAYS:
            with self.subTest(variant=variant):
                services = render(variant)
                native = self.phase_job(services, "native")
                for dependency in (native, PG_BOOTSTRAP):
                    self.assert_gate(
                        services,
                        "fi-collector",
                        dependency,
                        "service_completed_successfully",
                    )
                consumer = "fi-property-catalog-consumer"
                self.assertEqual(
                    set(services[consumer]["depends_on"]),
                    {INDEX_BOOTSTRAP, "property-catalog-topic-init"},
                )
                for dependency in services[consumer]["depends_on"]:
                    self.assert_gate(
                        services, consumer, dependency, "service_completed_successfully"
                    )

    def test_runtime_literal_guards_survive_operator_and_split_database_overrides(self):
        for variant in OVERLAYS:
            for overrides in ({}, OPERATOR_OVERRIDES):
                with self.subTest(variant=variant, overrides=bool(overrides)):
                    services = render(variant, full=True, overrides=overrides)
                    for runtime in (*RUNTIMES, "peerdb-temporal-init"):
                        env = services[runtime]["environment"]
                        self.assertEqual(
                            env.get("NO_STARTUP_DB_MUTATIONS"), "true", runtime
                        )
                        self.assertEqual(
                            env.get("FI_SKIP_CH25_MIGRATION"), "1", runtime
                        )

    def test_dev_and_production_new_jobs_are_check_only(self):
        for variant in ("dev", "production"):
            with self.subTest(variant=variant):
                services = render(variant, overrides=OPERATOR_OVERRIDES)
                self.assertIn(PG_BOOTSTRAP, services.keys())
                postgres = services[PG_BOOTSTRAP]
                self.assert_python_job(postgres, ["python", "manage.py"])
                self.assertEqual(
                    argv(postgres),
                    ["python", "manage.py", "migrate", "--check", "--noinput"],
                )
                jobs = [PG_BOOTSTRAP, "peerdb-init", "peerdb-temporal-init"]
                jobs.extend(
                    self.phase_job(services, phase) for phase in ("native", "cdc")
                )
                peerdb = services["peerdb-init"]
                self.assert_python_job(peerdb, ["python", "-m", PEERDB_SETUP])
                self.assertEqual(
                    argv(peerdb),
                    [
                        "python",
                        "-m",
                        PEERDB_SETUP,
                        "--wait-for-mirrors",
                        "--timeout",
                        "900",
                    ],
                    "retained snapshots must be allowed to finish without CREATE",
                )
                for name in jobs:
                    job = services[name]
                    self.assertNotIn("--apply", argv(job), name)
                    self.assertEqual(job["image"], services["backend"]["image"], name)
                    if variant == "dev":
                        self.assertEqual(job["image"], "futureagi/future-agi:dev")
                        mounts = {
                            mount["target"]: mount for mount in job.get("volumes", [])
                        }
                        self.assertIn("/app/backend", mounts.keys(), name)
                        self.assertEqual(mounts["/app/backend"]["type"], "bind", name)
                        self.assertEqual(
                            mounts["/app/backend"]["source"],
                            str(ROOT / "futureagi"),
                            name,
                        )

    def test_production_jobs_and_all_workers_inherit_required_auth_values(self):
        required = (
            "SECRET_KEY",
            "AGENTCC_INTERNAL_API_KEY",
            "AGENTCC_ADMIN_TOKEN",
            "PG_PASSWORD",
            "MINIO_ROOT_PASSWORD",
            "RABBITMQ_USER",
            "RABBITMQ_PASSWORD",
        )
        services = render("production", full=True, overrides=OPERATOR_OVERRIDES)
        jobs = [PG_BOOTSTRAP, "peerdb-init", "peerdb-temporal-init"]
        jobs.extend(self.phase_job(services, phase) for phase in ("native", "cdc"))
        for name in (*RUNTIMES, *jobs):
            with self.subTest(service=name):
                env = services[name]["environment"]
                self.assertEqual(env.get("ENV_TYPE"), "production")
                for key in required:
                    self.assertEqual(env.get(key), PRODUCTION_ENV[key], key)
        for key in required:
            with self.subTest(missing=key), self.assertRaisesRegex(AssertionError, key):
                render("production", full=True, overrides={key: ""})


class InstallerStartupContractTests(unittest.TestCase):
    # Execute only the bounded bring-up block with an in-process Compose double.
    # Never run installer preflight, .env writes, builds, daemon calls or cleanup.
    def startup_block(self, name):
        source = (ROOT / "bin" / name).read_text()
        markers = (
            (
                "# ---------------- bring up ----------------",
                "# ---------------- readiness wait ----------------",
            )
            if name == "install"
            else ("# ---- bring up ----", "# ---- readiness wait ----")
        )
        return source.split(markers[0], 1)[1].split(markers[1], 1)[0]

    def test_both_installers_have_one_bounded_up_and_no_replay_or_cleanup(self):
        command = "up -d --build --wait --wait-timeout 1200"
        for name in ("install", "install.ps1"):
            with self.subTest(installer=name):
                block = self.startup_block(name)
                self.assertEqual(block.count(command), 1)
                code = "\n".join(
                    line
                    for line in block.splitlines()
                    if not line.lstrip().startswith("#")
                )
                self.assertNotRegex(
                    code, r"\b(?:until|while|for|foreach|sleep|Start-Sleep)\b"
                )
                for forbidden in (
                    "--force-recreate",
                    "--remove-orphans",
                    "--renew-anon-volumes",
                    " down",
                    " restart",
                    " rm",
                ):
                    self.assertNotIn(forbidden, code)
                self.assertIn("partial state retained", block)
                self.assertIn("no automatic retry", block)
        self.assertRegex(
            self.startup_block("install"), r"if ! \$DC up [^\n]+; then\s+die "
        )
        self.assertRegex(
            self.startup_block("install.ps1"),
            r"if \(\$LASTEXITCODE -ne 0\) \{\s+Die ",
        )

    def assert_attempt(self, result, status):
        expected = "COMPOSE:up -d --build --wait --wait-timeout 1200"
        calls = [
            line for line in result.stdout.splitlines() if line.startswith("COMPOSE:")
        ]
        self.assertEqual(calls, [expected], result.stdout + result.stderr)
        self.assertEqual(result.returncode == 0, status == 0, result.stderr)
        self.assertEqual("CONTINUED" in result.stdout, status == 0)
        if status:
            self.assertIn("partial state retained", result.stdout)
            self.assertIn("no automatic retry", result.stdout)
            self.assertNotIn("OK:", result.stdout)

    def test_shell_success_failure_and_timeout_never_replay_startup(self):
        preamble = """
set -euo pipefail
compose_status=$1
DC=mock_compose
mock_compose() { printf 'COMPOSE:%s\\n' "$*"; return "$compose_status"; }
step() { :; }
ok() { printf 'OK:%s\\n' "$*"; }
warn() { :; }
sleep() { :; }
die() { printf 'FAIL:%s\\n' "$*"; exit 1; }
"""
        for status in (0, 1, 124):
            with self.subTest(status=status):
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-c",
                        preamble
                        + self.startup_block("install")
                        + "\nprintf 'CONTINUED\\n'",
                        "startup-test",
                        str(status),
                    ],
                    env={"PATH": "/nonexistent"},
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                self.assert_attempt(result, status)

    @unittest.skipUnless(
        shutil.which("pwsh"), "pwsh unavailable; static contract still runs"
    )
    def test_powershell_success_failure_and_timeout_never_replay_startup(self):
        preamble = """
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
function Invoke-Compose {
  Write-Output ('COMPOSE:' + ($args -join ' '))
  $global:LASTEXITCODE = $script:ComposeStatus
}
function Step { }
function Ok { Write-Output ('OK:' + ($args -join ' ')) }
function Warn { }
function Start-Sleep { }
function Die { Write-Output ('FAIL:' + ($args -join ' ')); exit 1 }
"""
        for status in (0, 1, 124):
            with self.subTest(status=status):
                result = subprocess.run(
                    [
                        shutil.which("pwsh"),
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        f"$script:ComposeStatus = {status}\n"
                        + preamble
                        + self.startup_block("install.ps1")
                        + "\nWrite-Output 'CONTINUED'",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assert_attempt(result, status)


if __name__ == "__main__":
    unittest.main()
