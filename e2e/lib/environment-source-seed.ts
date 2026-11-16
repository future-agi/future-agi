import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveBackendContainer } from './simulate-seed';

export interface EnvironmentSourceSeed {
  environmentId: string;
  environmentName: string;
  environmentVersionId: string;
  suiteVersionId: string;
  scenarioKeys: string[];
}

const SEED_SCRIPT = String.raw`
import io
import json
import os
import tarfile

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models import HostedHarnessJob
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.harness_versioning.versions import record_authoring


def archive():
    scenarios = [
        {
            "name": "source policy success",
            "scenario_key": "source-policy-success",
            "instruction": "Answer the customer using the reviewed source policy.",
            "tests": "Use the reviewed policy and records.",
            "max_turns": 4,
        },
        {
            "name": "source policy refusal",
            "scenario_key": "source-policy-refusal",
            "instruction": "Refuse a request forbidden by the reviewed source policy.",
            "tests": "Apply the reviewed refusal rule.",
            "max_turns": 4,
        },
    ]
    files = {
        "contract.json": json.dumps({"agent": "support", "tools": []}).encode(),
        "schema.sql": b"create table customers(customer_id text primary key);",
        "scenarios.json": json.dumps(scenarios).encode(),
        "runtime-validation.json": json.dumps({"status": "passed"}).encode(),
        "environment-bundle/manifest.json": json.dumps({"files": []}).encode(),
    }
    for scenario in scenarios:
        folder = scenario["scenario_key"]
        files[f"scenarios/{folder}/scenario.json"] = json.dumps(scenario).encode()
        files[f"scenarios/{folder}/ready.py"] = b"def ready(world):\n    return True\n"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as bundle:
        for path, data in files.items():
            member = tarfile.TarInfo(path)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    return out.getvalue()


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    environment_name = f"T20 source E2E {suffix}"
    payload = {
        "schema_version": "futureagi.harness-job.v1",
        "source": {"kind": "github", "repository": "e2e/source-agent", "ref": "main", "visibility": "public"},
        "agent": {"connector": "auto", "config": {}, "secret_refs": {}},
        "scenario_count": 2,
        "seed": 42,
        "runtime": {"isolation": "dedicated_vm", "cpu_units": 1, "memory_mb": 1024, "parallelism": 1, "concurrency_weight": 1, "max_duration_seconds": 600, "network_policy": "none"},
        "security": {"untrusted_source": True, "read_only_source": True, "allow_privileged": False, "allow_host_runtime_control": False, "allowed_egress_domains": []},
        "retry": {"max_infrastructure_attempts": 0, "initial_backoff_seconds": 1, "max_backoff_seconds": 1, "retryable_domains": []},
        "artifacts": {"level": "full", "retention_days": 30, "allow_bundle_download": False, "max_artifact_bytes": 1048576},
        "metadata": {"name": environment_name},
    }
    environment, _ = create_hosted_job(
        organization,
        payload,
        idempotency_key=f"t20-source-{suffix}",
        workspace=workspace,
    )
    recorded = record_authoring(environment, archive(), origin="authoring")
    environment.state = HostedHarnessJob.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["state", "current_stage", "updated_at"])
    print(json.dumps({
        "environmentId": str(environment.id),
        "environmentName": environment_name,
        "environmentVersionId": str(recorded.environment.id),
        "suiteVersionId": str(recorded.suite.id),
        "scenarioKeys": ["source-policy-success", "source-policy-refusal"],
    }))


main()
`;

export function seedEnvironmentForSources(opts: {
  organizationId: string;
  workspaceId: string;
}): EnvironmentSourceSeed {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), 'e2e-t20-source-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, SEED_SCRIPT);
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/seed-${suffix}.py`]);
    const stdout = execFileSync('docker', [
      'exec',
      '-e', `SEED_ORG_ID=${opts.organizationId}`,
      '-e', `SEED_WORKSPACE_ID=${opts.workspaceId}`,
      '-e', `SEED_SUFFIX=${suffix}`,
      container,
      'sh',
      '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    return JSON.parse(stdout.trim().split('\n').pop() ?? '') as EnvironmentSourceSeed;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
