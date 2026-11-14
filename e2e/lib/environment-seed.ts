import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveBackendContainer } from './simulate-seed';

/**
 * Seeds a built, versioned Simulate environment in the caller's org and workspace.
 *
 * The product makes an environment only through a builder build, which needs the
 * simulation runner and a sandbox; this harness starts neither. So, as
 * `lib/simulate-seed.ts` does for call logs, this runs the platform's own services
 * inside the selected stack's backend container, in the order a build reaches them:
 * `create_hosted_job` (the job row; it dispatches nothing), `register_attempt` and
 * `provision_scenarios` (the scenario index), then `record_authoring` on a minimal
 * authoring archive packed by `archives.pack_archive`, which writes the environment
 * and suite versions and their bundles to object storage and makes them the default
 * pair. Stage outputs pinned to that pair let the environment page open as built.
 * The sequence is the one `simulate/tests/test_harness_versioning_rollback.py::_built`
 * uses; a change to those services breaks this seeder loudly, at the call.
 *
 * Needs HARNESS_VERSIONING_ENABLED on the stack (both e2e overlays set it).
 */
export interface EnvironmentSeed {
  environmentId: string;
  name: string;
  scenarioNames: string[];
  scenarioKeys: string[];
  environmentVersionId: string;
  suiteVersionId: string;
}

const SEED_SCRIPT = `
import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django

django.setup()

from minio.error import S3Error

from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

# Parallel workers seed at once, and the platform creates the upload bucket on its first
# write by checking, then creating. Create it here first: a racing create's
# BucketAlreadyOwnedByYou means another worker made it, which is success.
try:
    ensure_bucket(get_storage_client(), UPLOAD_BUCKET_NAME)
except S3Error as exc:
    if exc.code != "BucketAlreadyOwnedByYou":
        raise

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models import HostedHarnessJob, HostedHarnessStageOutput
from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.hosted_harness import (
    create_hosted_job,
    pinned_stage_outputs,
    provision_scenarios,
    register_attempt,
)

SCENARIOS = ("order_status_lookup", "refund_request")


def key(name):
    return name.replace("_", "-")


def payload():
    # The v1 job request; the source is never fetched: a seeded environment never builds.
    refs = {
        name: {"key": f"harness-{name.lower()}", "manager": "platform-vault",
               "purpose": "target_provider", "version": "1"}
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
    }
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {"kind": "github", "repository": "acme/agent", "ref": "main", "visibility": "public"},
        "agent": {"connector": "auto", "config": {}, "secret_refs": refs},
        "scenario_count": len(SCENARIOS),
        "seed": 42,
        "runtime": {"isolation": "dedicated_vm", "cpu_units": 4, "memory_mb": 8192, "parallelism": 1,
                    "concurrency_weight": 1, "max_duration_seconds": 600, "network_policy": "live"},
        "security": {"untrusted_source": True, "read_only_source": True, "allow_privileged": False,
                     "allow_host_runtime_control": False, "allowed_egress_domains": ["api.example.com"]},
        "retry": {"max_infrastructure_attempts": 2, "initial_backoff_seconds": 1, "max_backoff_seconds": 15,
                  "retryable_domains": ["infrastructure"]},
        "artifacts": {"level": "full", "retention_days": 30, "allow_bundle_download": False,
                      "max_artifact_bytes": 1024},
        "metadata": {},
    }


def authoring():
    documents = [
        {"name": n, "scenario_key": key(n), "instruction": f"call about {n}", "tests": "t"}
        for n in SCENARIOS
    ]
    files = {
        "contract.json": json.dumps({"agent": "support", "tools": []}),
        "sub_goals.json": json.dumps(
            {"sub_goals": [{"name": "looks_up_order", "what": "looks up the order before answering"}]}
        ),
        "schema.sql": "create table orders(id int);",
        "handlers/lookup.py": "def handle(db):\\n    return {}\\n",
        "scenarios.json": json.dumps(documents),
        "environment-bundle/manifest.json": json.dumps({"files": []}),
        "runtime-validation.json": json.dumps({"status": "passed"}),
    }
    for document in documents:
        folder = f"scenarios/{document['name']}"
        files[f"{folder}/scenario.json"] = json.dumps(document)
        files[f"{folder}/ready.py"] = "def ready(world):\\n    return True\\n"
    return archives.pack_archive({path: text.encode() for path, text in files.items()})


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    name = os.environ["SEED_NAME"]

    environment, _ = create_hosted_job(
        organization, payload(), idempotency_key=f"e2e-environment-seed-{suffix}", workspace=workspace
    )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(name=name)
    attempt = register_attempt(environment.id, endpoint_base_url="https://e2e-seed.invalid").attempt
    provision_scenarios(attempt, {
        "operation": "provision",
        "name": "Suite",
        "modality": "voice",
        "personas": [
            {"scenario_key": key(n), "name": n, "role": "customer", "situation": "s", "outcome": "o",
             "persona": {"name": n}}
            for n in SCENARIOS
        ],
    })
    environment.refresh_from_db()
    data = dict(environment.payload)
    data["metadata"] = {**(data.get("metadata") or {}),
                        "authoring_object_key": f"harness/environments/e2e-seed-{suffix}.tar.gz"}
    environment.payload = data
    environment.state = HostedHarnessJob.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])

    record_authoring(environment, authoring(), origin="authoring")
    environment.refresh_from_db()
    if environment.environment_version_id is None or environment.suite_version_id is None:
        raise SystemExit("environment seed: record_authoring made no versions; is HARNESS_VERSIONING_ENABLED on?")

    pinned = pinned_stage_outputs(environment.environment_version, environment.suite_version)
    for output in [*pinned.values(), {"kind": "environment", "title": "Execution environment", "data": {"a": 1}}]:
        HostedHarnessStageOutput.no_workspace_objects.create(
            job=environment, kind=output["kind"], title=output["title"],
            summary=output.get("summary") or "", data=output["data"],
        )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(stage_outputs=list(pinned.values()))

    print(json.dumps({
        "environmentId": str(environment.id),
        "name": name,
        "scenarioNames": list(SCENARIOS),
        "scenarioKeys": [key(n) for n in SCENARIOS],
        "environmentVersionId": str(environment.environment_version_id),
        "suiteVersionId": str(environment.suite_version_id),
    }))


main()
`;

export function seedEnvironment(opts: {
  organizationId: string;
  workspaceId: string;
  name: string;
}): EnvironmentSeed {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), 'e2e-env-seed-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, SEED_SCRIPT);
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/env-seed-${suffix}.py`]);
    const stdout = execFileSync('docker', ['exec',
      '-e', `SEED_ORG_ID=${opts.organizationId}`,
      '-e', `SEED_WORKSPACE_ID=${opts.workspaceId}`,
      '-e', `SEED_SUFFIX=${suffix}`,
      '-e', `SEED_NAME=${opts.name}`,
      container, 'sh', '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/env-seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    const lastLine = stdout.trim().split('\n').pop() ?? '';
    return JSON.parse(lastLine) as EnvironmentSeed;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
