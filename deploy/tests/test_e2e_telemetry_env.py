"""E2E stacks render every Django service with the loopback telemetry URL.

Render Compose and Helm inputs; no container or cluster is started.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

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


def job_env(workflow: str, job: str) -> dict:
    path = ROOT / '.github/workflows' / workflow
    return yaml.safe_load(path.read_text())['jobs'][job]['env']


def retained_compose(directory: Path, tag: str) -> str:
    path = directory / 'docker-compose.yml'
    path.write_bytes(subprocess.check_output(['git', 'show', f'{tag}:docker-compose.yml'], cwd=ROOT))
    return str(path)


class E2ETelemetryEnvironmentTests(unittest.TestCase):
    def assert_loopback(self, configured: dict, expected: tuple[str, ...]):
        self.assertTrue(set(expected) <= set(configured), sorted(configured))
        for name, environment in configured.items():
            self.assertEqual(environment["FUTURE_AGI_TELEMETRY_URL"], LOOPBACK, name)
            self.assertEqual(
                environment.get("FUTURE_AGI_TELEMETRY_DISABLED"), "true", name
            )

    def test_ci_job_settings_reach_current_and_retained_containers(self):
        standalone = job_env('standalone-ci.yml', 'standalone-install')
        upgrade = job_env('standalone-ci.yml', 'upgrade')
        boot = job_env('update-real-boot-smoke.yml', 'boot')
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.touch()
            for env, file, tag, service in (
                (standalone, 'docker-compose.yml', None, 'app'),
                (upgrade, 'docker-compose.distributed.yml', None, 'backend'),
                (upgrade, None, upgrade['FROM_VERSION'], 'backend'),
                (boot, None, boot['A_VERSION'], 'app'),
            ):
                with self.subTest(file=file, tag=tag):
                    if tag:
                        file = retained_compose(Path(tmp), tag)
                    settings = {k: v for k, v in env.items() if k.startswith('FUTURE_AGI_TELEMETRY_')}
                    services = render(str(env_file), (file,), settings)['services']
                    self.assert_loopback({service: services[service]['environment']}, (service,))

    def test_rehearsal_generated_env_reaches_retained_app(self):
        from deploy.tests.update_e2e.real_automatic import ProductHarness
        from scripts.update_rehearsal_milestone import SnapshotMilestone
        from scripts.update_rehearsal_shipping import ShippingRehearsal

        class EnvWritten(Exception):
            """Stop at host setup, after generating .env but before starting Docker."""

        tag = job_env('update-real-boot-smoke.yml', 'boot')['A_VERSION']
        for prepare in (SnapshotMilestone.install_and_boot, ShippingRehearsal._prepare, ProductHarness.prepare):
            with self.subTest(harness=prepare.__qualname__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                file = retained_compose(root, tag)
                (root / '.env.example').write_text('FUTURE_AGI_TELEMETRY_URL=https://example.invalid\n')
                harness = Mock(root=root, project='rehearsal', source_version=tag, source_digest='sha256:' + 'a' * 64,
                               source_manifest=None, ports={}, _secret_values=set())
                harness.budgets = SimpleNamespace(startup_seconds=600, stability_seconds=5)
                harness.row.deployment_directory = root
                harness.row.execution_deployment = {'files': {}}
                harness.row.release.manifest = {'version': tag}
                harness.docker.return_value.stdout = b''
                harness.dc.side_effect = EnvWritten
                with patch('deploy.update.processes.ProcessSupervisor', side_effect=EnvWritten), \
                     self.assertRaises(EnvWritten):
                    if prepare is ProductHarness.prepare:
                        prepare(harness, harness.source_digest, version=tag)
                    else:
                        prepare(harness)
                environment = render(str(root / '.env'), (file,))['services']['app']['environment']
                self.assert_loopback({'app': environment}, ('app',))

    def test_manual_rehearsal_checks_both_retained_layouts_before_boot(self):
        from deploy.tests.compose_test_tools import compose_command

        harness = ROOT / 'deploy/tests/update_e2e/rehearse_real_standalone.sh'
        helpers = harness.with_name('standalone_rehearsal_env.sh')
        # Exercise the helpers below without starting a stack. Also ensure the
        # entry point still calls the guard before each install/update boot.
        source = harness.read_text().split('pin_standalone_telemetry "$CHECKOUT/.env"', 1)[1]
        for version, boot in (
            ('FROM_VERSION', 'run 20-install.log'),
            ('TO_VERSION', 'run 55-apply.log'),
            ('TO_VERSION', 'run 58-install-again.log'),
            ('FROM_VERSION', 'paste_command 74-rollback.log'),
            ('TO_VERSION', 'run 85-apply-staged.log'),
        ):
            before, marker, source = source.partition(boot)
            self.assertTrue(marker, boot)
            self.assertIn(f'require_standalone_telemetry "${version}"', before, boot)
        for tag in ('v1.51.0', 'v1.52.0'):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                file = retained_compose(root, tag)
                env_file = root / '.env'
                env_file.write_bytes(subprocess.check_output(['git', 'show', f'{tag}:.env.example'], cwd=ROOT))
                command = [
                    'bash', '-ec',
                    'source "$1"; shift; pin_standalone_telemetry .env; check_standalone_telemetry "$PWD" "$@"',
                    'rehearsal', str(helpers), tag, *compose_command(),
                ]
                subprocess.run(command, cwd=root, env=host_env(EXPORTED), check=True)
                app = render(str(env_file), (file,))['services']['app']['environment']
                self.assert_loopback({'app': app}, ('app',))
                for key, value in EXPORTED.items():
                    with self.subTest(key=key):
                        override = {'services': {'app': {'environment': {key: value}}}}
                        (root / 'docker-compose.override.yml').write_text(yaml.safe_dump(override))
                        result = subprocess.run(command, cwd=root, env=host_env(EXPORTED), capture_output=True)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, b'')

    def test_rehearsal_refuses_retained_app_that_ignores_either_setting(self):
        from scripts.update_rehearsal_shipping import require_rehearsal_telemetry

        settings = {'FUTURE_AGI_TELEMETRY_URL': LOOPBACK, 'FUTURE_AGI_TELEMETRY_DISABLED': 'true'}
        for key in settings:
            for value in (None, EXPORTED[key]):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'loopback'):
                    env = dict(settings, **{key: value})
                    require_rehearsal_telemetry({'services': {'app': {'environment': env}}})

    def test_kind_and_gitops_workloads_use_loopback(self):
        from deploy.tests.helm_gitops.acceptance import CHART, Fixture

        overlay = 'ci/telemetry-loopback.yaml'
        self.assertIn(f'-f "$here/{overlay}"', (CHART / 'hack/kind-smoke.sh').read_text())
        fixture = Mock(registry_host='localhost:5000', token='unit')
        Fixture.create_secrets(fixture)
        for harness, values in (
            ('kind', ['-f', str(CHART / 'examples/bundled.yaml'), '-f', str(CHART / overlay)]),
            ('gitops', ['-f', '-']),
        ):
            with self.subTest(harness=harness):
                result = subprocess.run(['helm', 'template', 'unit', str(CHART), *values], check=True,
                                        input=yaml.safe_dump(fixture.values) if harness == 'gitops' else None,
                                        capture_output=True, text=True)
                seen = set()
                for doc in yaml.safe_load_all(result.stdout):
                    if not doc or doc['kind'] not in ('Deployment', 'Job'):
                        continue
                    pod = doc['spec']['template']['spec']
                    for container in pod.get('initContainers', []) + pod['containers']:
                        name = container['name']
                        if name in ('collectstatic', 'backend', 'worker', 'bootstrap'):
                            env = {row['name']: row.get('value') for row in container['env']}
                            self.assert_loopback({name: env}, (name,))
                            seen.add(name)
                self.assertEqual(seen, {'collectstatic', 'backend', 'worker', 'bootstrap'})

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
            "metrics-schema-bootstrap",
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
