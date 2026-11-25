import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveBackendContainer } from './simulate-seed';

/**
 * Seeds an environment whose design waits for approval, in the caller's org and workspace.
 *
 * The product reaches this state only after a reading of the agent, which needs a sandbox and a
 * model this harness starts neither of. So, as `lib/environment-seed.ts` does for a built
 * environment, this runs the platform's own services inside the backend container in the order a
 * reading leaves them: a connected agent version with its stored read contract
 * (`store_version_contract`), a received job (`create_hosted_job`), then `create_design_review`,
 * which copies the contract to the environment and parks the job at `awaiting_approval`. A change
 * to those services breaks this seeder loudly, at the call.
 */
export interface DesignReviewSeed {
  environmentId: string;
  agentVersionId: string;
  name: string;
  tools: string[];
}

const SEED_SCRIPT = `
import json
import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django

django.setup()

from minio.error import S3Error

from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

try:
    ensure_bucket(get_storage_client(), UPLOAD_BUCKET_NAME)
except S3Error as exc:
    if exc.code != "BucketAlreadyOwnedByYou":
        raise

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, AgentVersion, AgentVersionSetup, HostedHarnessJob
from simulate.services.agent_contracts import store_version_contract
from simulate.services.harness_design_review import create_design_review
from simulate.services.hosted_harness import create_hosted_job

CONTRACT = {
    "data_schema": {
        "BOOKINGS": {"type": "object", "properties": {"id": {}, "guest": {}}},
        "HOTELS": {"type": "object"},
    },
    "tools": [
        {"name": "check_otp", "args": ["phone"], "arg_types": {"phone": "text"}},
        {"name": "issue_refund", "args": ["amount"], "requires": ["check_otp"]},
        {"name": "notify", "args": ["refund_id"], "inputs_from": {"refund_id": "issue_refund.id"}},
        {"name": "find_hotels", "effects": "reads HOTELS"},
    ],
    "hard_constraints": ["Run check_otp before any refund", "Refunds over 25 need a manager"],
    "real_use_cases": ["Refund a damaged item", "Cancel a booking", "Change address"],
    "reading_inputs": {"commit": "a" * 40},
}


def payload():
    refs = {
        name: {"key": f"harness-{name.lower()}", "manager": "platform-vault",
               "purpose": "target_provider", "version": "1"}
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
    }
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {"kind": "github", "repository": "acme/agent", "ref": "main", "visibility": "public"},
        "agent": {"connector": "auto", "config": {}, "secret_refs": refs},
        "scenario_count": 3,
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


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    name = os.environ["SEED_NAME"]

    definition = AgentDefinition.objects.create(
        agent_name=name, agent_type=AgentDefinition.AgentTypeChoices.TEXT, inbound=True,
        organization=organization, workspace=workspace,
    )
    version = AgentVersion.no_workspace_objects.create(
        agent_definition=definition, organization=organization, workspace=workspace,
        description="e2e design review", commit_message="e2e design review", status="active",
    )
    AgentVersionSetup.no_workspace_objects.create(agent_version=version, status=AgentVersionSetup.Status.STORED)
    if not store_version_contract(organization_id=organization.id, agent_version_id=version.id, contract=CONTRACT):
        raise SystemExit("design review seed: the agent version's read contract was not stored")

    environment, _ = create_hosted_job(
        organization, payload(), idempotency_key=f"e2e-design-review-seed-{suffix}", workspace=workspace,
        state=HostedHarnessJob.State.RECEIVED,
    )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(name=name)
    environment.refresh_from_db()
    create_design_review(environment, SimpleNamespace(agent_version_id=version.id), CONTRACT)

    print(json.dumps({
        "environmentId": str(environment.id),
        "agentVersionId": str(version.id),
        "name": name,
        "tools": [tool["name"] for tool in CONTRACT["tools"]],
    }))


main()
`;

export function seedDesignReview(opts: {
  organizationId: string;
  workspaceId: string;
  name: string;
}): DesignReviewSeed {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), 'e2e-design-review-seed-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, SEED_SCRIPT);
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/design-review-seed-${suffix}.py`]);
    const stdout = execFileSync('docker', ['exec',
      '-e', `SEED_ORG_ID=${opts.organizationId}`,
      '-e', `SEED_WORKSPACE_ID=${opts.workspaceId}`,
      '-e', `SEED_SUFFIX=${suffix}`,
      '-e', `SEED_NAME=${opts.name}`,
      container, 'sh', '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/design-review-seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    const lastLine = stdout.trim().split('\n').pop() ?? '';
    return JSON.parse(lastLine) as DesignReviewSeed;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
