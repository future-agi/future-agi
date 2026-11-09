"""Offline r2 regressions: synthetic env only; no Docker, Helm or dotenv reads."""

import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import yaml

from test_distributed_hub_profile import active_services, services, shell_function
import test_helm_template_security as helm_guard

ROOT = Path(__file__).resolve().parents[2]
MARKER = "OMEGA_HUB_COMPOSE_PROFILES"
TOKEN_KEY = "OMEGA_HUB_CHECKER_TOKEN"


class OverlayLoader(yaml.SafeLoader):
    """Read tagged values for source assertions, NOT a Compose merge emulator."""


def tagged_value(loader, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


for tag in ("!override", "!reset"):
    OverlayLoader.add_constructor(tag, tagged_value)


def production():
    return yaml.load((ROOT / "deploy/docker-compose.production.yml").read_text(), Loader=OverlayLoader)


class HubProfileRepairTests(unittest.TestCase):
    def test_every_distributed_django_consumer_receives_profile_marker(self):
        model = services()
        consumers = {name: svc for name, svc in model.items()
                     if TOKEN_KEY in svc.get("environment", {}) and name != "hub-checker"}
        self.assertIn("backend", consumers)
        self.assertTrue({name for name in model if name.startswith("worker")} <= consumers.keys())
        for name, svc in consumers.items():
            with self.subTest(service=name):
                self.assertEqual(svc["environment"].get(MARKER), "${COMPOSE_PROFILES:-}")

    def test_production_pins_marker_for_every_distributed_django_consumer(self):
        overlay = production()
        self.assertIn(overlay["x-production-backend-env"].get(MARKER), ("hub", "*"))
        for name, svc in services().items():
            if TOKEN_KEY in svc.get("environment", {}) and name != "hub-checker":
                with self.subTest(service=name):
                    self.assertIn(overlay["services"].get(name, {}).get("environment", {}).get(MARKER), ("hub", "*"))

    def test_distributed_no_profile_disables_real_checker_accessor(self):
        django_conf = ModuleType("django.conf")
        django_conf.settings = SimpleNamespace()
        env = {TOKEN_KEY: "synthetic-checker-for-profile-contract"}
        marker = services()["backend"]["environment"].get(MARKER)
        if marker is not None:
            self.assertEqual(marker, "${COMPOSE_PROFILES:-}")
            env[MARKER] = ""  # COMPOSE_PROFILES unset: Compose's :- default.
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, {"django": ModuleType("django"), "django.conf": django_conf}
        ):
            conf = runpy.run_path(str(ROOT / "futureagi/omega_hub/conf.py"))
            self.assertIsNone(conf["checker_token"]())
            os.environ[MARKER] = "hub"
            self.assertEqual(conf["checker_token"](), env[TOKEN_KEY])

    def test_standalone_default_has_no_privileged_or_host_userns_services(self):
        self.assertEqual([
            name for name, svc in active_services(services("docker-compose.yml")).items()
            if svc.get("privileged") is True or svc.get("userns_mode") == "host"
        ], [])

    def test_static_default_read_only_set(self):
        self.assertEqual({name for name, svc in active_services(services()).items() if svc.get("read_only")},
                         {"fi-collector", "fi-property-catalog-consumer", "code-executor"})

    def test_static_production_hub_profiles_use_external_checker(self):
        overlay = production()["services"]
        for profiles in (("hub",), ("hub", "full")):
            with self.subTest(profiles=profiles):
                selected = active_services(overlay, profiles)
                self.assertNotIn("hub-runner", selected)
                self.assertIn("hub-checker", selected)
                self.assertTrue(selected["hub-checker"]["environment"]["DOCKER_HOST"].startswith(
                    "${OMEGA_HUB_RUNNER_DOCKER_HOST:?"))
        # !reset / !override tags are independently pinned in test_distributed_hub_profile.

    def test_changelog_explains_opt_in_and_existing_containers(self):
        text = (ROOT / "CHANGELOG.md").read_text().split("## [", 1)[0]
        self.assertNotIn("adds hub-checker and hub-runner as default-start services", text)
        for fact in ("opt-in", "COMPOSE_PROFILES=hub", "./bin/install --distributed",
                     "Neither starts by default", "does not stop", "external runner"):
            with self.subTest(fact=fact):
                self.assertIn(fact, text)

    def test_docs_describe_distributed_profile_and_nonprivileged_executor(self):
        runbook = (ROOT / "futureagi/omega_hub/ops/RUNBOOK.md").read_text()
        self.assertNotIn("Helm and Distributed omit", runbook)
        self.assertIn("Standalone and Distributed", runbook)
        readiness = (ROOT / "futureagi/omega_rollout/readiness.py").read_text()
        self.assertIn("Standalone and Distributed", readiness)
        reference = (ROOT / "deploy/env-reference.toml").read_text()
        self.assertNotIn("privileged nsjail", reference)
        self.assertNotIn("Distributed runs both services", reference)
        self.assertIn("Standalone and Distributed", reference)
        self.assertNotIn("Requires privileged: true", (ROOT / "docker-compose.distributed.yml").read_text())


class HelmGuardRepairTests(unittest.TestCase):
    def test_m1_templated_default_true_is_unsafe(self):
        for key in ("privileged", "hostPID", "hostNetwork", "hostIPC"):
            with self.subTest(key=key):
                self.assertTrue(helm_guard.unsafe_literals(f"{key}: {{{{ .Values.flag | default true }}}}"))

    def test_m2_dict_keys_are_unsafe(self):
        for key in ("privileged", "hostPID", "hostNetwork", "hostIPC"):
            for expression in (f'{{{{ toYaml (dict "{key}" true) }}}}', f"{{{{ dict `{key}` false }}}}",
                               f'{{{{ dict "other" false "{key}" true }}}}'):
                with self.subTest(expression=expression):
                    self.assertTrue(helm_guard.unsafe_literals(expression))

    def test_only_unquoted_literal_false_is_allowed(self):
        for key in ("privileged", "hostPID", "hostNetwork", "hostIPC"):
            for value in ("1", "null", "yes", "on", "False", '"false"', "", "*alias", "false {{ .Values.extra }}"):
                with self.subTest(key=key, value=value):
                    self.assertTrue(helm_guard.unsafe_literals(f"{key}: {value}"))
            self.assertFalse(helm_guard.unsafe_literals(f"{key}: false # safe\n"))
            self.assertFalse(helm_guard.unsafe_literals(f"securityContext: {{{key}: false}}"))

    def test_m3_partial_and_extensionless_mutations_fail_real_chart_guard(self):
        for name in ("_helpers.tpl", "unsafe-snippet", "extra.txt", "pod.yml"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                chart = Path(directory)
                (chart / "templates/code-executor").mkdir(parents=True)
                (chart / "templates/code-executor/deployment.yaml").write_text("privileged: false\n")
                (chart / "templates/NOTES.txt").write_text("privileged: true (documentation only)\n")
                mutation = chart / "templates" / name
                mutation.write_text('{{- define "unsafe" -}}\nprivileged: true\n{{- end -}}\n')
                with patch.object(helm_guard, "CHART", chart):
                    suite = unittest.TestSuite([helm_guard.HelmTemplateSecurityTests(
                        "test_all_yaml_templates_exclude_privileged_and_host_namespace_literals")])
                    result = unittest.TestResult()
                    suite.run(result)
                self.assertEqual(result.errors, [])
                self.assertTrue(result.failures, "mutation must fail the actual chart guard")

    def test_notes_and_schema_name_both_unsupported_pod_security_levels(self):
        schema = json.loads((helm_guard.CHART / "values.schema.json").read_text())
        for text in ((helm_guard.CHART / "templates/NOTES.txt").read_text(),
                     schema["properties"]["codeExecutor"]["properties"]["enabled"]["description"]):
            with self.subTest(document="NOTES" if "\n" in text else "schema"):
                for fact in ("Baseline and Restricted", "NET_ADMIN", "Unconfined", "privileged-level", "exemption"):
                    self.assertIn(fact, text)


class InstallerOrphanWarningTests(unittest.TestCase):
    def probe(self, shell_profiles, file_profiles, distributed, existing):
        installer = (ROOT / "bin/install").read_text()
        function = shell_function(installer, "warn_disabled_hub_containers")
        self.assertRegex(installer, r"(?m)^warn_disabled_hub_containers\s*$")
        script = "\n".join([
            "set -euo pipefail", (ROOT / "bin/lib/env.sh").read_text(),
            'env_text() { [[ "$1" == COMPOSE_PROFILES ]] && printf "%s" "$FILE_PROFILES"; }',
            'project_has_service() { grep -qx -- "$1" <<<"$PROJECT_SERVICES"; }',
            'warn() { printf "%s\\n" "$*"; }',
            'docker() { printf "unexpected Docker call" >&2; exit 99; }',
            'DC="docker compose"', 'project_name="fixture-project"', function,
            "warn_disabled_hub_containers",
        ])
        env = {"PATH": os.defpath, "FILE_PROFILES": file_profiles,
               "DISTRIBUTED": str(int(distributed)), "PROJECT_SERVICES": "\n".join(existing)}
        if shell_profiles is not None:
            env["COMPOSE_PROFILES"] = shell_profiles
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["/bin/bash", "-c", script], cwd=directory, env=env,
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_disabled_profile_warns_for_either_retained_service_and_prints_exact_command(self):
        for existing in (("hub-runner",), ("hub-checker",), ("hub-runner", "hub-checker")):
            for shell, file in ((None, ""), (None, "full"), ("full", "hub"), ("", "hub"), ("hub-extra", "")):
                with self.subTest(existing=existing, shell=shell, file=file):
                    output = self.probe(shell, file, True, existing)
                    self.assertIn("does not stop", output)
                    self.assertIn("docker compose -f docker-compose.distributed.yml -p fixture-project --profile hub rm -sf hub-runner hub-checker", output)
                    self.assertIn("Production", output)

    def test_selected_hub_fresh_install_and_standalone_do_not_warn(self):
        for shell, file, distributed, existing in (
            (None, "hub", True, ("hub-runner",)), ("full,hub", "", True, ("hub-checker",)),
            ("*", "", True, ("hub-runner",)), (" full, hub ", "", True, ("hub-runner",)),
            (None, "", True, ()), (None, "", False, ("hub-runner",)),
        ):
            with self.subTest(shell=shell, file=file, distributed=distributed, existing=existing):
                self.assertEqual(self.probe(shell, file, distributed, existing), "")

    def test_powershell_warning_is_wired_and_never_removes_automatically(self):
        text = (ROOT / "bin/install.ps1").read_text()
        match = re.search(r"(?ms)^function Warn-DisabledHubContainers \{.*?^\}", text)
        self.assertIsNotNone(match)
        function = match.group()
        self.assertRegex(text, r"(?m)^Warn-DisabledHubContainers\s*$")
        for fact in ("$IsDistributed", "COMPOSE_PROFILES", "Get-EnvValue", "'hub'", "'*'",
                     "'hub-runner'", "'hub-checker'", "does not stop", "--profile hub rm -sf hub-runner hub-checker"):
            self.assertIn(fact, function)
        self.assertNotRegex(function, r"(?m)^\s*(Invoke-Compose|&\s*\$DcCmd|docker)\b")


if __name__ == "__main__":
    unittest.main()
