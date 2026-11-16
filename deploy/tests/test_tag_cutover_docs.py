"""Executable runbook contracts; synthetic CLI stubs, never a deployment.

These test the procedure's writer barrier, not an application feature flag.
The real-store reconciliation suite separately demonstrates why both early
new-editor exposure and a stale first save after reconciliation are unsafe.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "deploy/helm/futureagi/UPGRADING.md"
STORES = {
    "postgres",
    "clickhouse",
    "redis",
    "minio",
    "temporal",
    "peerdb-catalog",
    "peerdb-temporal",
    "peerdb-minio",
    "property-catalog-kafka",
}


def section(path, heading, end):
    return path.read_text().split(heading, 1)[1].split(end, 1)[0]


def blocks(text):
    return re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.DOTALL)


class TagCutoverDocsTests(unittest.TestCase):
    def assert_order(self, text, *steps):
        position = 0
        for step in steps:
            position = text.index(step, position) + len(step)

    def test_compose_and_standalone_do_not_expose_editor_before_final_review(self):
        distributed = section(
            ROOT / "deploy/README.md",
            "### Upgrading a production install",
            "### Signed updates",
        )
        standalone = section(GUIDE, "## Standalone", "## Argo CD")
        for text, stop, start in (
            (distributed, "$COMPOSE --profile '*' stop $WRITERS", "$COMPOSE up -d"),
            (standalone, "docker compose stop app", "docker compose up -d"),
        ):
            self.assert_order(
                text,
                stop,
                "reconcile_legacy_trace_tags --dry-run",
                "STOP for human review",
                "reconcile_legacy_trace_tags\n",
                "reconcile_legacy_trace_tags --dry-run",
                "Only after final reconciliation is complete",
                start,
            )
            # A paste of a single dry-run block must not silently start apply or
            # restart the app. Approval is outside the executable fence.
            for code in blocks(text):
                if "reconcile_legacy_trace_tags --dry-run" in code:
                    self.assertNotRegex(code, r"reconcile_legacy_trace_tags\s*\n")
                    self.assertNotIn(" up -d", code)
                subprocess.run(["bash", "-n"], input=code, text=True, check=True)

    def test_all_profiles_and_writer_services_are_stopped_without_datastores(self):
        compose = yaml.safe_load((ROOT / "docker-compose.distributed.yml").read_text())
        services = list(compose["services"])
        text = section(
            ROOT / "deploy/README.md",
            "### Upgrading a production install",
            "### Signed updates",
        )
        code = next(code for code in blocks(text) if "WRITERS=" in code)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cli = directory / "docker"
            cli.write_text(
                "#!/usr/bin/env python3\nimport json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['CALLS'], 'a') as out: out.write(json.dumps(args) + '\\n')\n"
                f"services = {services!r}\n"
                "if 'config' in args:\n"
                "    assert args[:3] == ['compose', '--profile', '*']\n"
                "    print('\\n'.join(services))\n"
            )
            cli.chmod(0o700)
            calls = directory / "calls.jsonl"
            env = {
                **os.environ,
                "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                "CALLS": str(calls),
            }
            subprocess.run(
                ["bash", "-c", 'COMPOSE="docker compose"\n' + code], env=env, check=True
            )
            issued = [json.loads(line) for line in calls.read_text().splitlines()]
            stopped = set(next(call[4:] for call in issued if call[3] == "stop"))
            self.assertEqual(stopped, set(services) - STORES)
            self.assertTrue(
                {
                    "backend",
                    "frontend",
                    "fi-collector",
                    "agentcc-gateway",
                    "worker-trace-ingestion",
                    "worker-tasks-l",
                    "peerdb-flow-worker",
                }
                <= stopped
            )
            self.assertFalse(stopped & STORES)
        standalone = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        self.assertTrue(
            {"app", "postgres", "clickhouse"} <= standalone["services"].keys()
        )

    def test_helm_scaling_covers_actual_workers_not_fictitious_zero_replica_values(
        self,
    ):
        text = section(GUIDE, "## Direct Helm", "## Standalone")
        self.assert_order(
            text,
            "delete hpa -l app.kubernetes.io/instance=futureagi",
            "scale deployment -l app.kubernetes.io/instance=futureagi --replicas=0",
            "STOP and verify",
            "reconcile_tags --dry-run\n",
            "STOP for human review",
            "reconcile_tags\n",
            "reconcile_tags --dry-run\n",
            "helm upgrade futureagi",
        )
        worker = (
            ROOT / "deploy/helm/futureagi/templates/worker/deployment.yaml"
        ).read_text()
        self.assertIn('"replicas" ($q.replicas | default 1)', worker)
        self.assertIn('"suffix" "worker-sql-workbench"', worker)
        self.assertIn('"suffix" "worker-exact-aggregation"', worker)
        labels = (ROOT / "deploy/helm/futureagi/templates/_helpers.tpl").read_text()
        self.assertIn("app.kubernetes.io/instance: {{ .root.Release.Name }}", labels)
        hpa = (ROOT / "deploy/helm/futureagi/templates/_scaling.tpl").read_text()
        self.assertIn('include "futureagi.componentLabels"', hpa)
        self.assertIn("per-queue `replicas: 0` is", text)
        self.assertIn("source of truth", text)

    def test_helm_dry_run_creates_only_one_nonmutating_job_and_keeps_operator_env(self):
        text = section(GUIDE, "## Direct Helm", "## Standalone")
        code = next(code for code in blocks(text) if "reconcile_tags()" in code)
        subprocess.run(["bash", "-n"], input=code, text=True, check=True)
        fixture = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "futureagi-bootstrap",
                "annotations": {"helm.sh/hook": "pre-upgrade"},
            },
            "spec": {
                "activeDeadlineSeconds": 1080,
                "ttlSecondsAfterFinished": 300,
                "template": {
                    "spec": {
                        "serviceAccountName": "futureagi-bootstrap",
                        "containers": [
                            {
                                "name": "bootstrap",
                                "image": "synthetic@sha256:abc",
                                "env": [
                                    {"name": "SERVICE_TYPE", "value": "bootstrap"},
                                    {
                                        "name": "STARTUP_DB_MUTATION_MODE",
                                        "value": "operator",
                                    },
                                ],
                            }
                        ],
                    }
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            # Force the snippet's python3 to the test interpreter (with PyYAML).
            (directory / "python3").symlink_to(sys.executable)
            helm = directory / "helm"
            helm.write_text(f"#!{sys.executable}\nprint({json.dumps(fixture)!r})\n")
            helm.chmod(0o700)
            kubectl = directory / "kubectl"
            kubectl.write_text(
                f"#!{sys.executable}\nimport json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['CALLS'], 'a') as out: out.write(json.dumps(args) + '\\n')\n"
                "if 'create' in args:\n"
                "    with open(os.environ['JOB'], 'w') as out: out.write(sys.stdin.read())\n"
                "if 'logs' in args: print('DRY RUN: scanned 1; would mirror 1; changed during scan 0')\n"
            )
            kubectl.chmod(0o700)
            env = {
                **os.environ,
                "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                "CALLS": str(directory / "calls"),
                "JOB": str(directory / "job"),
            }
            subprocess.run(
                ["bash", "-c", code],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            issued = [
                json.loads(line)
                for line in (directory / "calls").read_text().splitlines()
            ]
            self.assertEqual(sum("create" in args for args in issued), 1)
            job = yaml.safe_load((directory / "job").read_text())
            container = job["spec"]["template"]["spec"]["containers"][0]
            self.assertEqual(
                container["command"],
                ["python", "manage.py", "reconcile_legacy_trace_tags"],
            )
            self.assertEqual(container["args"], ["--dry-run"])
            self.assertEqual(
                container["env"],
                fixture["spec"]["template"]["spec"]["containers"][0]["env"],
            )
            self.assertNotIn("annotations", job["metadata"])
            self.assertNotIn("activeDeadlineSeconds", job["spec"])
            self.assertEqual(job["spec"]["backoffLimit"], 0)

    def test_every_entrypoint_rejects_online_handoff_and_links_recovery(self):
        for relative in (
            "INSTALLATION.md",
            "deploy/README.md",
            "deploy/update/README.md",
            "deploy/helm/futureagi/README.md",
            "deploy/helm/futureagi/UPGRADING.md",
        ):
            text = (ROOT / relative).read_text()
            self.assertNotIn("run the second apply immediately", text)
            self.assertNotRegex(text, r"reconcile_tags --dry-run[^\n]*&&")
            self.assertIn("tracer_entity_tag_assignment", text)
            self.assertIn("reverse", text)
            self.assertIn("reload", text)
        barrier = section(GUIDE, "## Legacy tag cutover barrier", "## Direct Helm")
        for requirement in (
            "all legacy and new editor/request writers",
            "No",
            "--after-id",
            "any",
            "missing",
            "revision-zero",
            "queued",
            "old tabs",
            "before",
            "recovery",
        ):
            self.assertIn(requirement.lower(), barrier.lower())
        self.assertIn(
            "If you cannot ensure\n   fresh clients, keep editing unavailable", barrier
        )


if __name__ == "__main__":
    unittest.main()
